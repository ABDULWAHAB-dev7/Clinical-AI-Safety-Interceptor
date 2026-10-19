"""Clinical endpoints from API.md: decompose (5.1) and verify (6.1).

Auth runs before body validation, so an unauthenticated caller gets 401 and never sees schema details.
Not built yet: the proxy (4.x), audit (7.1), read (7.2) and idempotency replay (8.3). See README.
"""

import hashlib
import json
import re
import uuid
from datetime import timezone
from typing import Literal, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.categories import classification_for, explanation, hallucination_category
from core.claims import claim_id_for, decompose_claims, overall_severity, summary
from core.contracts import AtomicProposition, AttributionSpan, MedicalEntity, VerificationResult
from core.entities import entity_from_model
from core.errors import PipelineError
from core.pipeline import PIPELINE_VERSION
from core.verify import VerificationError, verify_claims
from store.records import EventRecord, StageRecord
from .auth import require_scope
from .context import load_patient_context
from .errors import InterceptionError

router = APIRouter(prefix="/v1/interceptor")

PATIENT_RE = r"^pid_[0-9a-f]{64}$"
ENCOUNTER_RE = r"^eid_[0-9a-f]{64}$"
CLAIM_RE = r"^clm_[0-9a-f]{12}$"
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
NOTE_TEXT_MAX = 200_000  # code points (API.md 13)
DECOMPOSER_VERSION = f"decomposer-rules-{PIPELINE_VERSION}"
VERIFIER_VERSION = f"verifier-{PIPELINE_VERSION}+knowledge-seed"


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

    store = request.app.state.store
    scope = _idempotency_scope(request.state.client_id, idempotency_key)
    body_sha = _digest(body.model_dump(mode="json"))
    prior = _store_call(store.find_idempotency, scope)
    if prior is not None and prior["body_sha256"] != body_sha:
        raise InterceptionError("IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used with a different body.",
                                "Idempotency-Key")
    note_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    claims, warnings = decompose_claims(text, note_sha, note_id="", knowledge=request.app.state.knowledge,
                                        include_entities=body.options.include_entities)
    if len(claims) > body.options.max_claims:
        raise InterceptionError("INTERCEPT_CLAIM_LIMIT_EXCEEDED", "The note yields more claims than max_claims.",
                                "options.max_claims")

    extract = StageRecord(
        stage="EXTRACT",
        component_name="decompose-rules",
        component_version=DECOMPOSER_VERSION,
        input_sha256=note_sha,
        output_payload={
            "assertion_count": len(claims),
            "skipped": len(warnings),
            # an index of IDs and offsets only: claim text is not stored (DATABASE.md 3.6)
            "claims": [{"claim_id": c["claim_id"], "char_start": c["char_start"], "char_end": c["char_end"],
                        "source_sentence_start": c["source_sentence_start"],
                        "source_sentence_end": c["source_sentence_end"]} for c in claims],
            "idempotency": {"scope": scope, "body_sha256": body_sha},
        },
        status="OK",
    )
    try:
        registration = store.register_note(
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
    state = request.app.state
    raw = body.fhir_bundle
    triples, warnings = load_patient_context(raw, body.patient_pseudo_id, state)

    if body.note_id is not None:
        owner = _note_owner(state.store, str(body.note_id))
        if owner is None:
            raise InterceptionError("NOTE_NOT_FOUND", "The note is unknown or not visible to the caller.", "note_id")
        if owner != body.patient_pseudo_id:
            raise InterceptionError("AUDIT_NOTE_PATIENT_MISMATCH", "note_id belongs to a different patient.",
                                    "patient_pseudo_id")

    claims = [
        {"claim_id": c.claim_id, "claim_text": c.claim_text, "char_start": c.char_start,
         "entities": [entity_from_model(e) for e in c.entities]}
        for c in (body.claims or [])
    ]
    try:
        outcome = verify_claims(claims, triples, state.knowledge, VERIFIER_VERSION, state.nli_model)
    except VerificationError:
        raise InterceptionError("INTERCEPT_VERIFIER_UNAVAILABLE",
                                "A claim cannot be verified without the NLI verifier. No content was released.")
    results = outcome.results

    verify_trace_id = None
    if body.note_id is not None:
        stage = StageRecord(
            stage="VERIFY",
            component_name="verifier",
            component_version=VERIFIER_VERSION,
            input_sha256=_digest([c["claim_id"] for c in claims]),
            output_payload={"results": _count_by(results, "nli_class"), "claims": outcome.traces},
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
        "warnings": warnings,
    }



# ---- audit and reads -----------------------------------------------------------------------------

class AuditOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    create_events_for: list[Literal["CRITICAL", "WARNING"]] = Field(default_factory=lambda: ["CRITICAL", "WARNING"])


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
    state = request.app.state
    store = state.store
    if not idempotency_key or not UUID4_RE.match(idempotency_key):
        raise InterceptionError("VALIDATION_FAILED", "Idempotency-Key must be a UUIDv4.", "Idempotency-Key")
    scope = _idempotency_scope(request.state.client_id, idempotency_key)
    body_sha = _digest(body.model_dump(mode="json"))

    prior = _store_call(store.find_idempotency, scope)
    if prior is not None:
        if prior["body_sha256"] != body_sha:
            raise InterceptionError("IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used with a different body.",
                                    "Idempotency-Key")
        report = _store_call(store.get_report, prior["report_id"]) if prior.get("report_id") else None
        if report is not None:  # a replay never writes a second set of events
            return JSONResponse(_report_view(report, replay=True), status_code=200)

    note_id = str(body.note_id)
    note = _store_call(store.get_note, note_id)
    if note is None:
        raise InterceptionError("NOTE_NOT_FOUND", "The note is unknown or not visible to the caller.", "note_id")
    if note["patient_pseudo_id"] != body.patient_pseudo_id:
        raise InterceptionError("AUDIT_NOTE_PATIENT_MISMATCH", "note_id belongs to a different patient.",
                               "patient_pseudo_id")

    claims = {c.claim_id: c for c in body.claims}
    results = {r.claim_id: r for r in body.results}
    if len(claims) != len(body.claims) or len(results) != len(body.results) or set(claims) != set(results):
        raise InterceptionError("AUDIT_CLAIM_MISMATCH", "Every claim needs exactly one result, and every result "
                                "a claim.", "results")
    _check_claims_belong_to_note(store, note, note_id, claims)
    for trace_id in body.verify_trace_ids:
        trace = _store_call(store.get_trace, str(trace_id))
        if trace is None or trace["stage"] != "VERIFY" or trace["note_id"] != note_id:
            raise InterceptionError("AUDIT_CLAIM_MISMATCH", "A verify trace does not exist for this note.",
                                    "verify_trace_ids")

    spans = _spans(body, claims, results, note)
    knowledge = state.knowledge
    create_for = set(body.options.create_events_for)
    events = []
    for claim_id, result in results.items():
        severity = result.risk_severity.value
        if severity == "SAFE" or severity not in create_for:
            continue
        claim = claims[claim_id]
        category = hallucination_category(result, claim.claim_text, [entity_from_model(e) for e in claim.entities],
                                          knowledge)
        events.append(EventRecord(
            claim_id=claim_id, category=category, risk_severity=severity, nli_class=result.nli_class.value,
            confidence=round(result.confidence, 3), claim_text=f"claim:{category.lower()}",
            risk_rule=result.risk_rule, matched_fhir_ref=result.matched_fhir_ref,
            doc_char_start=claim.char_start, doc_char_end=claim.char_end,
        ))

    result_dicts = [r.model_dump(mode="json") for r in body.results]
    pipeline_status = "BLOCKED" if any(e.risk_severity == "CRITICAL" for e in events) else "VERIFIED"
    report_id = str(uuid.uuid4())
    stage = StageRecord(
        stage="ATTRIBUTE",
        component_name="audit-report",
        component_version=VERIFIER_VERSION,
        input_sha256=body_sha,
        output_payload={
            "report_id": report_id,
            "overall_risk_severity": overall_severity(result_dicts),
            "counts": summary(result_dicts)["by_severity"],
            "pipeline_status": pipeline_status,
            "verify_trace_ids": [str(t) for t in body.verify_trace_ids],
            "results": result_dicts,
            "attribution": spans,
            "idempotency": {"scope": scope, "body_sha256": body_sha},
        },
        status="OK",
    )
    try:
        refs = store.write_audit_report(note_id=note_id, stage=stage, events=events,
                                        pipeline_status=pipeline_status, claim_count=len(body.claims))
    except PipelineError:
        raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")

    event_by_claim = {claim_id: (event_id, detected_at) for claim_id, event_id, detected_at in refs.events}
    report = {
        "trace_id": refs.trace_id, "note_id": note_id, "patient_pseudo_id": body.patient_pseudo_id,
        "payload": stage.output_payload, "created_at": refs.created_at,
        "events": [
            {"event_id": event_by_claim[e.claim_id][0], "claim_id": e.claim_id, "category": e.category,
             "risk_severity": e.risk_severity, "nli_class": e.nli_class, "risk_rule": e.risk_rule,
             "confidence": e.confidence, "matched_fhir_ref": e.matched_fhir_ref,
             "detected_at": event_by_claim[e.claim_id][1]}
            for e in sorted(events, key=lambda e: e.claim_id)
        ],
    }
    return JSONResponse(_report_view(report, replay=False), status_code=201,
                        headers={"Location": f"/v1/interceptor/audit/reports/{report_id}"})


@router.get("/notes/{note_id}", dependencies=[require_scope("read")])
def read_note(note_id: str, request: Request):
    store = request.app.state.store
    note = _store_call(store.get_note, note_id) if _is_uuid(note_id) else None
    if note is None:
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
        "received_at": _iso(note["received_at"]),
        "completed_at": _iso(note["completed_at"]),
        # claim text is not stored, so claims are returned as an index of IDs and offsets
        "claims": index,
    }


@router.get("/audit/reports/{report_id}", dependencies=[require_scope("read")])
def read_report(report_id: str, request: Request):
    report = _store_call(request.app.state.store.get_report, report_id) if _is_uuid(report_id) else None
    if report is None:
        raise InterceptionError("REPORT_NOT_FOUND", "The report is unknown or not visible to the caller.", "report_id")
    return _report_view(report, replay=False)


def _check_claims_belong_to_note(store, note, note_id, claims):
    index = _store_call(store.get_claim_index, note_id)
    known = {c["claim_id"]: c for c in (index or [])}
    for claim in claims.values():
        recorded = known.get(claim.claim_id)
        derived = claim_id_for(note["note_sha256"], claim.char_start, claim.char_end, claim.claim_text)
        if (str(claim.note_id) != note_id or recorded is None or derived != claim.claim_id
                or (recorded["char_start"], recorded["char_end"]) != (claim.char_start, claim.char_end)
                or claim.char_end > note["char_length"]):
            raise InterceptionError("AUDIT_CLAIM_MISMATCH", "A claim does not belong to this note.", "claims")


def _spans(body, claims, results, note):
    """Attribution spans, without the explanation text (it is generated again whenever the report is read)."""
    result_ids = {str(r.result_id): r.claim_id for r in body.results}
    if body.attribution is None:
        return [
            {"span_id": str(uuid.uuid4()), "doc_char_start": claims[cid].char_start,
             "doc_char_end": claims[cid].char_end, "classification": classification_for(r.nli_class),
             "claim_id": cid, "result_id": str(r.result_id)}
            for cid, r in sorted(results.items())
        ]
    spans = []
    for span in body.attribution:
        if (str(span.note_id) != str(body.note_id) or span.doc_char_end > note["char_length"]
                or (span.claim_id is not None and span.claim_id not in claims)
                or (span.result_id is not None and str(span.result_id) not in result_ids)):
            raise InterceptionError("AUDIT_CLAIM_MISMATCH", "An attribution span does not match the note or its "
                                    "claims.", "attribution")
        spans.append({"span_id": str(span.span_id), "doc_char_start": span.doc_char_start,
                      "doc_char_end": span.doc_char_end, "classification": span.classification.value,
                      "claim_id": span.claim_id, "result_id": str(span.result_id) if span.result_id else None})
    return spans


def _report_view(report, replay):
    payload = report["payload"]
    by_result = {r["result_id"]: r for r in payload["results"]}
    return {
        "report_id": payload["report_id"],
        "note_id": report["note_id"],
        "patient_pseudo_id": report["patient_pseudo_id"],
        "overall_risk_severity": payload["overall_risk_severity"],
        "pipeline_status": payload["pipeline_status"],
        "counts": payload["counts"],
        "hallucination_events": [
            {"event_id": e["event_id"], "claim_id": e["claim_id"], "category": e["category"],
             "risk_severity": e["risk_severity"], "nli_class": e["nli_class"], "risk_rule": e["risk_rule"],
             "confidence": round(float(e["confidence"]), 3), "matched_fhir_ref": e["matched_fhir_ref"],
             "detected_at": _iso(e["detected_at"])}
            for e in report["events"]
        ],
        "attribution_spans": [
            {"span_id": s["span_id"], "note_id": report["note_id"], "doc_char_start": s["doc_char_start"],
             "doc_char_end": s["doc_char_end"], "classification": s["classification"], "claim_id": s["claim_id"],
             "result_id": s["result_id"],
             "explanation": explanation(s["classification"], by_result.get(s["result_id"]))}
            for s in payload["attribution"]
        ],
        "trace_id": report["trace_id"],
        "created_at": _iso(report["created_at"]),
        "idempotent_replay": replay,
    }


def _store_call(fn, *args):
    """Store errors are fail-closed: the caller gets 503 AUDIT_WRITE_FAILED and no content."""
    try:
        return fn(*args)
    except PipelineError:
        raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")


def _idempotency_scope(client_id, key):
    return hashlib.sha256(f"{client_id}:{key}".encode("utf-8")).hexdigest()


def _is_uuid(value):
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _iso(value):
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _note_owner(store, note_id):
    return store.note_patient(note_id)


def _count_by(rows, key):
    counts = {}
    for row in rows:
        counts[row[key]] = counts.get(row[key], 0) + 1
    return counts


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()
