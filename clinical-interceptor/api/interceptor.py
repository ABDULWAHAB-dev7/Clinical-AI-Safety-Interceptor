"""Clinical endpoints from API.md: decompose (5.1) and verify (6.1).

Auth runs before body validation, so an unauthenticated caller gets 401 and never sees schema details.
Not built yet: the proxy (4.x), audit (7.1), read (7.2) and idempotency replay (8.3). See README.
"""

import hashlib
import json
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.claims import (
    VerificationError,
    decompose_claims,
    overall_severity,
    summary,
    verify_claims,
)
from core.errors import ErrorClass, PipelineError
from core.pipeline import PIPELINE_VERSION
from core.pseudo import patient_pseudo_id
from fhir.bundle import parse_bundle
from store.records import StageRecord
from .auth import require_scope
from .errors import InterceptionError

router = APIRouter(prefix="/v1/interceptor")

PATIENT_RE = r"^pid_[0-9a-f]{64}$"
ENCOUNTER_RE = r"^eid_[0-9a-f]{64}$"
CLAIM_RE = r"^clm_[0-9a-f]{12}$"
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
NOTE_TEXT_MAX = 200_000  # code points (API.md 13)
BUNDLE_ENTRY_MAX = 500  # API.md 13
DECOMPOSER_VERSION = f"decomposer-rules-{PIPELINE_VERSION}"
VERIFIER_VERSION = f"deterministic-verifier-{PIPELINE_VERSION}"


# ---- decompose -----------------------------------------------------------------------------------

class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_system: str = Field(min_length=1, max_length=64)
    note_type: str = Field(min_length=1, max_length=64)
    text: str  # length is checked in the handler so that over-limit notes return 413, not 400


class DecomposeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_claims: int = Field(default=200, ge=1, le=500)
    include_entities: bool = True


class DecomposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_pseudo_id: str = Field(pattern=PATIENT_RE)
    encounter_pseudo_id: Optional[str] = Field(default=None, pattern=ENCOUNTER_RE)
    note: NoteInput
    options: DecomposeOptions = Field(default_factory=DecomposeOptions)


@router.post("/decompose", dependencies=[require_scope("decompose")])
def decompose_note(
    body: DecomposeRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key or not UUID4_RE.match(idempotency_key):
        raise InterceptionError("VALIDATION_FAILED", "Idempotency-Key must be a UUIDv4.", "Idempotency-Key")
    text = body.note.text
    if not text.strip():
        raise InterceptionError("VALIDATION_FAILED", "Note text must not be empty.", "note.text")
    if len(text) > NOTE_TEXT_MAX:
        raise InterceptionError("INTERCEPT_NOTE_TOO_LARGE", "Note text exceeds 200,000 code points.", "note.text")

    note_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    claims, warnings = decompose_claims(text, note_sha, note_id="")
    if len(claims) > body.options.max_claims:
        raise InterceptionError("INTERCEPT_CLAIM_LIMIT_EXCEEDED", "The note yields more claims than max_claims.",
                                "options.max_claims")

    extract = StageRecord(
        stage="EXTRACT",
        component_name="decompose-rules",
        component_version=DECOMPOSER_VERSION,
        input_sha256=note_sha,
        output_payload={"assertion_count": len(claims), "skipped": len(warnings)},
        status="OK",
    )
    try:
        registration = request.app.state.store.register_note(
            patient_pseudo_id=body.patient_pseudo_id,
            encounter_pseudo_id=body.encounter_pseudo_id,
            source_system=body.note.source_system,
            note_type=body.note.note_type,
            note_sha256=note_sha,
            char_length=len(text),
            extract=extract,
        )
    except PipelineError:
        raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")

    for claim in claims:
        claim["note_id"] = registration.note_id
    payload = {
        "note_id": registration.note_id,
        "patient_pseudo_id": body.patient_pseudo_id,
        "encounter_pseudo_id": body.encounter_pseudo_id,
        "note_sha256": note_sha,
        "char_length": len(text),
        "claim_count": len(claims),
        "decompose_trace_id": registration.decompose_trace_id,
        "component_version": DECOMPOSER_VERSION,
        "warnings": warnings,
        "claims": claims,
    }
    headers = {"Location": f"/v1/interceptor/notes/{registration.note_id}"} if registration.created else {}
    return JSONResponse(payload, status_code=201 if registration.created else 200, headers=headers)


# ---- verify --------------------------------------------------------------------------------------

class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str = Field(pattern=CLAIM_RE)
    claim_text: str = Field(min_length=1, max_length=512)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    entities: list = Field(default_factory=list)

    @field_validator("char_end")
    @classmethod
    def _end_after_start(cls, value, info):
        start = info.data.get("char_start")
        if start is not None and value <= start:
            raise ValueError("char_end must be greater than char_start")
        return value


class PolicyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_confidence_entailment: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class VerifyApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_pseudo_id: str = Field(pattern=PATIENT_RE)
    note_id: Optional[uuid.UUID] = None
    fhir_bundle: dict
    claims: Optional[list[ClaimInput]] = Field(default=None, max_length=500)
    policy: Optional[PolicyOverride] = None

    @field_validator("claims")
    @classmethod
    def _unique_ids(cls, value):
        if value is not None and len({c.claim_id for c in value}) != len(value):
            raise ValueError("claim_id values must be unique")
        return value


@router.post("/verify", dependencies=[require_scope("verify")])
def verify(body: VerifyApiRequest, request: Request):
    state = request.app.state
    raw = body.fhir_bundle
    if raw.get("type") != "collection":
        raise InterceptionError("INTERCEPT_BUNDLE_INVALID", "fhir_bundle.type must be 'collection'.", "fhir_bundle.type")
    entries = raw.get("entry", [])
    if len(entries) > BUNDLE_ENTRY_MAX:
        raise InterceptionError("INTERCEPT_BUNDLE_TOO_LARGE", "The bundle exceeds 500 entries.", "fhir_bundle.entry")
    try:
        bundle = parse_bundle(raw, encounter_ref="request-body")
    except PipelineError:
        raise InterceptionError("INTERCEPT_BUNDLE_INVALID", "fhir_bundle is not a valid FHIR Bundle.", "fhir_bundle")
    if bundle.parse_failures:
        raise InterceptionError("INTERCEPT_BUNDLE_INVALID", "A resource in fhir_bundle fails validation.",
                                "fhir_bundle.entry")
    if not bundle.patient_mrn:
        raise InterceptionError("INTERCEPT_BUNDLE_INVALID", "fhir_bundle has no Patient identifier.", "fhir_bundle")
    if patient_pseudo_id(bundle.patient_mrn, state.pseudo_key) != body.patient_pseudo_id:
        raise InterceptionError("INTERCEPT_PATIENT_MISMATCH",
                                "The patient in fhir_bundle does not match patient_pseudo_id.",
                                "patient_pseudo_id")

    if body.note_id is not None:
        owner = _note_owner(state.store, str(body.note_id))
        if owner is None:
            raise InterceptionError("NOTE_NOT_FOUND", "The note is unknown or not visible to the caller.", "note_id")
        if owner != body.patient_pseudo_id:
            raise InterceptionError("AUDIT_NOTE_PATIENT_MISMATCH", "note_id belongs to a different patient.",
                                    "patient_pseudo_id")

    claims = [c.model_dump() for c in (body.claims or [])]
    try:
        results = verify_claims(claims, bundle, state.policy, VERIFIER_VERSION)
    except VerificationError:
        raise InterceptionError("INTERCEPT_VERIFIER_UNAVAILABLE",
                                "A claim cannot be verified without the NLI verifier. No content was released.")

    verify_trace_id = None
    if body.note_id is not None:
        stage = StageRecord(
            stage="VERIFY",
            component_name="verifier",
            component_version=VERIFIER_VERSION,
            input_sha256=_digest([c["claim_id"] for c in claims]),
            output_payload={"results": _count_by(results, "nli_class")},
            status="OK",
        )
        try:
            verify_trace_id = state.store.record_verify(str(body.note_id), stage)
        except PipelineError:
            raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")

    return {
        "verification_context_sha256": _digest(raw),
        "verifier_version": VERIFIER_VERSION,
        "verify_trace_id": verify_trace_id,
        "overall_risk_severity": overall_severity(results),
        "summary": summary(results),
        "results": results,
        "warnings": [],
    }


def _note_owner(store, note_id):
    return store.note_patient(note_id)


def _count_by(rows, key):
    counts = {}
    for row in rows:
        counts[row[key]] = counts.get(row[key], 0) + 1
    return counts


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()
