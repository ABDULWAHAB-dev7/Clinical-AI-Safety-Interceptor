"""HTTP surface (FastAPI). Native verification endpoint plus health probes.

Not built yet: the OpenAI/Anthropic-compatible proxy endpoints (the upstream provider is undecided) and the
override and review routes (approver roles and the review-store retention are open items, architecture 10.1).
"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.pipeline import MAX_DOCUMENT_CHARS, verify_document
from core.policy import load_policy
from store.audit import AuditLog

ROOT = Path(__file__).resolve().parent.parent


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_text: str = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)
    doc_type: str = Field(min_length=1, max_length=64)
    encounter_ref: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")


def create_app(*, policy_path, fhir_dir, audit_path, nli_model=None):
    policy = load_policy(policy_path)  # refuses to start without a valid policy (architecture 7.2 #5)
    audit = AuditLog(audit_path)
    app = FastAPI(title="Clinical Interceptor", version=policy.version)

    @app.post("/v1/verify")
    def verify(body: VerifyRequest):
        if body.doc_type not in policy.doc_types_in_scope:
            raise HTTPException(status_code=400, detail="document type is not in facility scope")
        result = verify_document(
            body.document_text,
            body.doc_type,
            body.encounter_ref,
            policy=policy,
            fhir_dir=fhir_dir,
            audit=audit,
            nli_model=nli_model,
        )
        return _render(result)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        return {
            "ready": True,
            "policy_version": policy.version,
            "nli_model": "loaded" if nli_model is not None else "not_loaded",
        }

    return app


def _render(result):
    payload = {
        "request_id": result.request_id,
        "outcome": result.outcome.value,
        "delivered": result.delivered,
        "policy_version": result.decision.policy_version,
        "rules_fired": result.decision.rules_fired,
        "errors": result.errors,
        "verdicts": [r.model_dump(mode="json") for r in result.records],
    }
    if result.delivered:  # blocked documents return no text (architecture 6.2)
        payload["spans"] = [s.model_dump(mode="json") for s in result.spans]
    return payload


def build_default_app():
    """Uvicorn factory: `uvicorn api.app:build_default_app --factory --host 127.0.0.1`."""
    return create_app(
        policy_path=os.environ.get("INTERCEPTOR_POLICY", str(ROOT / "config/policy/default.yaml")),
        fhir_dir=os.environ.get("INTERCEPTOR_FHIR_DIR", str(ROOT / "fixtures/fhir")),
        audit_path=os.environ.get("INTERCEPTOR_AUDIT", str(ROOT / "var/audit.jsonl")),
    )
