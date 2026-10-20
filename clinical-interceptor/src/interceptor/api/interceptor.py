"""Clinical endpoints from API.md: decompose (5.1), verify (6.1), audit (7.1) and reads (7.2).

These handlers check the request, apply idempotency and map results to status codes. The decisions are made in
api/engine.py, which the legacy /v1/verify route also calls.

Auth runs before body validation, so an unauthenticated caller gets 401 and never sees schema details.
"""

import hashlib
import re
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from interceptor.core.contracts import AtomicProposition, AttributionSpan, MedicalEntity, VerificationResult

from . import engine
from .auth import authorize_patient, can_see_patient, require_scope
from .engine import _store_call, digest, iso, report_view
from .errors import InterceptionError

router = APIRouter(prefix="/v1/interceptor")

PATIENT_RE = r"^pid_[0-9a-f]{64}$"
ENCOUNTER_RE = r"^eid_[0-9a-f]{64}$"
CLAIM_RE = r"^clm_[0-9a-f]{12}$"
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
NOTE_TEXT_MAX = 200_000  # code points (API.md 13)
MAX_CLAIMS_DEFAULT = 200


# ---- decompose -----------------------------------------------------------------------------------

class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_system: str = Field(min_length=1, max_length=64)
    note_type: str = Field(min_length=1, max_length=64)
    text: str  # length is checked in the handler so that over-limit notes return 413, not 400


class DecomposeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_claims: int = Field(default=MAX_CLAIMS_DEFAULT, ge=1, le=500)
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
    authorize_patient(request, body.patient_pseudo_id)
    if not idempotency_key or not UUID4_RE.match(idempotency_key):
        raise InterceptionError("VALIDATION_FAILED", "Idempotency-Key must be a UUIDv4.", "Idempotency-Key")
    text = body.note.text
    if not text.strip():
        raise InterceptionError("VALIDATION_FAILED", "Note text must not be empty.", "note.text")
    if len(text) > NOTE_TEXT_MAX:
        raise InterceptionError("INTERCEPT_NOTE_TOO_LARGE", "Note text exceeds 200,000 code points.", "note.text")

    state = request.app.state
    scope = _idempotency_scope(request.state.client_id, idempotency_key)
    body_sha = digest(body.model_dump(mode="json"))
    prior = _store_call(state.store.find_idempotency, scope)
    if prior is not None and prior["body_sha256"] != body_sha:
        raise InterceptionError("IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used with a different body.",
                                "Idempotency-Key")

    registration, payload = engine.decompose(
        state,
        patient_pid=body.patient_pseudo_id,
        encounter_pid=body.encounter_pseudo_id,
        text=text,
        source_system=body.note.source_system,
        note_type=body.note.note_type,
        include_entities=body.options.include_entities,
        max_claims=body.options.max_claims,
        idempotency={"scope": scope, "body_sha256": body_sha},
    )
    headers = {"Location": f"/v1/interceptor/notes/{registration.note_id}"} if registration.created else {}
    return JSONResponse(payload, status_code=201 if registration.created else 200, headers=headers)


# ---- verify --------------------------------------------------------------------------------------

class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str = Field(pattern=CLAIM_RE)
    claim_text: str = Field(min_length=1, max_length=512)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    entities: list[MedicalEntity] = Field(default_factory=list)

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
    authorize_patient(request, body.patient_pseudo_id)
    return engine.verify(
        request.app.state,
        patient_pid=body.patient_pseudo_id,
        raw_bundle=body.fhir_bundle,
        claims=body.claims or [],
        note_id=str(body.note_id) if body.note_id is not None else None,
    )


# ---- audit and reads -----------------------------------------------------------------------------

class AuditOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    create_events_for: list[Literal["CRITICAL", "WARNING"]] = Field(default_factory=lambda: ["CRITICAL", "WARNING"])  # type: ignore[arg-type]


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: uuid.UUID
    patient_pseudo_id: str = Field(pattern=PATIENT_RE)
    verify_trace_ids: list[uuid.UUID] = Field(max_length=20)
    claims: list[AtomicProposition] = Field(min_length=1, max_length=500)
    results: list[VerificationResult] = Field(max_length=1000)
    attribution: Optional[list[AttributionSpan]] = Field(default=None, max_length=1000)
    options: AuditOptions = Field(default_factory=AuditOptions)


@router.post("/audit", dependencies=[require_scope("audit_write")])
def audit(
    body: AuditRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    authorize_patient(request, body.patient_pseudo_id)
    state = request.app.state
    store = state.store
    if not idempotency_key or not UUID4_RE.match(idempotency_key):
        raise InterceptionError("VALIDATION_FAILED", "Idempotency-Key must be a UUIDv4.", "Idempotency-Key")
    scope = _idempotency_scope(request.state.client_id, idempotency_key)
    body_sha = digest(body.model_dump(mode="json"))

    prior = _store_call(store.find_idempotency, scope)
    if prior is not None:
        if prior["body_sha256"] != body_sha:
            raise InterceptionError("IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used with a different body.",
                                    "Idempotency-Key")
        report = _store_call(store.get_report, prior["report_id"]) if prior.get("report_id") else None
        if report is not None:  # a replay never writes a second set of events
            return JSONResponse(report_view(report, replay=True), status_code=200)

    view = engine.audit(
        state,
        patient_pid=body.patient_pseudo_id,
        note_id=str(body.note_id),
        verify_trace_ids=body.verify_trace_ids,
        claims=body.claims,
        results=body.results,
        attribution=body.attribution,
        create_for=set(body.options.create_events_for),
        body_sha=body_sha,
        scope=scope,
    )
    return JSONResponse(view, status_code=201,
                        headers={"Location": f"/v1/interceptor/audit/reports/{view['report_id']}"})


@router.get("/notes/{note_id}", dependencies=[require_scope("read")])
def read_note(note_id: str, request: Request):
    store = request.app.state.store
    note = _store_call(store.get_note, note_id) if _is_uuid(note_id) else None
    if note is None or not can_see_patient(request, note["patient_pseudo_id"]):
        raise InterceptionError("NOTE_NOT_FOUND", "The note is unknown or not visible to the caller.", "note_id")
    index = _store_call(store.get_claim_index, note["note_id"]) or []
    return {
        "note_id": note["note_id"],
        "patient_pseudo_id": note["patient_pseudo_id"],
        "encounter_pseudo_id": note["encounter_pseudo_id"],
        "source_system": note["source_system"],
        "note_type": note["note_type"],
        "note_sha256": note["note_sha256"],
        "char_length": note["char_length"],
        "claim_count": note["claim_count"],
        "pipeline_status": note["pipeline_status"],
        "received_at": iso(note["received_at"]),
        "completed_at": iso(note["completed_at"]),
        # claim text is not stored, so claims are returned as an index of IDs and offsets
        "claims": index,
    }


@router.get("/audit/reports/{report_id}", dependencies=[require_scope("read")])
def read_report(report_id: str, request: Request):
    report = _store_call(request.app.state.store.get_report, report_id) if _is_uuid(report_id) else None
    if report is None or not can_see_patient(request, report["patient_pseudo_id"]):
        raise InterceptionError("REPORT_NOT_FOUND", "The report is unknown or not visible to the caller.", "report_id")
    return report_view(report, replay=False)


def _idempotency_scope(client_id, key):
    return hashlib.sha256(f"{client_id}:{key}".encode()).hexdigest()


def _is_uuid(value):
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False
