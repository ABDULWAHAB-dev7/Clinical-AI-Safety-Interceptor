"""HTTP surface (FastAPI). Native verification endpoint plus health probes.

Not built yet: the OpenAI/Anthropic-compatible proxy endpoints (the upstream provider is undecided) and the
override and review routes (approver roles and the review-store retention are open items, architecture 10.1).

Configuration (environment, read by build_default_app; the service refuses to start without them):
  INTERCEPTOR_DATABASE_URL  PostgreSQL DSN for the audit schema (db/schema.sql must already be applied)
  INTERCEPTOR_PSEUDO_KEY    secret for patient and encounter pseudo-IDs (at least 16 bytes; lives in a KMS)
  INTERCEPTOR_POLICY        policy file (default config/policy/default.yaml)
  INTERCEPTOR_FHIR_DIR      encounter bundles (default fixtures/fhir)
"""

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.pipeline import MAX_DOCUMENT_CHARS, verify_document
from core.policy import load_policy

ROOT = Path(__file__).resolve().parent.parent


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_text: str = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)
    doc_type: str = Field(min_length=1, max_length=64)
    encounter_ref: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    source_system: str = Field(default="api", min_length=1, max_length=64)


def create_app(*, policy_path, fhir_dir, store, pseudo_key, nli_model=None):
    policy = load_policy(policy_path)  # refuses to start without a valid policy (architecture 7.2 #5)
    if not isinstance(pseudo_key, (bytes, bytearray)) or len(pseudo_key) < 16:
        raise ValueError("pseudonymization key must be at least 16 bytes")
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
            store=store,
            pseudo_key=bytes(pseudo_key),
            nli_model=nli_model,
            source_system=body.source_system,
        )
        return _render(result)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        database_ok = store.ping()
        body = {
            "ready": database_ok,
            "policy_version": policy.version,
            "database": "ok" if database_ok else "unreachable",
            "nli_model": "loaded" if nli_model is not None else "not_loaded",
        }
        return JSONResponse(body, status_code=200 if database_ok else 503)

    return app


def _render(result):
    payload = {
        "request_id": result.request_id,
        "outcome": result.outcome.value,
        "delivered": result.delivered,
        "pipeline_status": result.pipeline_status,
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
    from store.postgres import PostgresAuditStore

    dsn = os.environ.get("INTERCEPTOR_DATABASE_URL")
    key = os.environ.get("INTERCEPTOR_PSEUDO_KEY")
    if not dsn:
        raise RuntimeError("INTERCEPTOR_DATABASE_URL is required")
    if not key:
        raise RuntimeError("INTERCEPTOR_PSEUDO_KEY is required")
    return create_app(
        policy_path=os.environ.get("INTERCEPTOR_POLICY", str(ROOT / "config/policy/default.yaml")),
        fhir_dir=os.environ.get("INTERCEPTOR_FHIR_DIR", str(ROOT / "fixtures/fhir")),
        store=PostgresAuditStore(dsn),
        pseudo_key=key.encode("utf-8"),
    )
