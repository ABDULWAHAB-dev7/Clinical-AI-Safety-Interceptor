"""Response shapes from API.md, as strict models (extra fields are forbidden).

The service builds responses as plain dicts. These models are what the contract tests validate them against, so a
field that appears or disappears is caught. Enum values come from DATABASE.md and are closed sets.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from interceptor.core.contracts import MedicalEntity, VerificationResult

Severity = Literal["CRITICAL", "WARNING", "SAFE"]
Category = Literal["DRUG_ALLERGY_CONFLICT", "MEDICATION_DOSE_MISMATCH", "DIAGNOSIS_CONTRADICTION",
                   "LAB_VALUE_MISMATCH", "TEMPORAL_MISMATCH", "UNSUPPORTED_CLAIM", "OTHER"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorObject(Strict):
    type: Literal["clinical_interception_error"]
    code: str
    message: str
    param: Optional[str]
    clinical_impact: Literal["NONE", "DEGRADED", "BLOCKED"]
    fail_mode: Literal["CLOSED"]
    retryable: bool
    request_id: str
    trace_id: Optional[str]
    docs_url: str


class ErrorEnvelope(Strict):
    error: ErrorObject


class WarningObject(Strict):
    code: str
    message: str
    param: Optional[str]


class ClaimOut(Strict):
    claim_id: str
    note_id: str
    source_sentence: str
    source_sentence_start: int
    source_sentence_end: int
    claim_text: str
    char_start: int
    char_end: int
    entities: list[MedicalEntity]


class DecomposeResponse(Strict):
    note_id: str
    patient_pseudo_id: str
    encounter_pseudo_id: Optional[str]
    note_sha256: str
    char_length: int
    claim_count: int
    decompose_trace_id: Optional[str]
    component_version: str
    warnings: list[WarningObject]
    claims: list[ClaimOut]


class VerifySummary(Strict):
    total: int
    by_severity: dict[str, int]


class VerifyResponse(Strict):
    verification_context_sha256: str
    verifier_version: str
    verify_trace_id: Optional[str]
    overall_risk_severity: Severity
    summary: VerifySummary
    results: list[VerificationResult]
    warnings: list[WarningObject]


class EventOut(Strict):
    event_id: str
    claim_id: str
    category: Category
    risk_severity: Literal["CRITICAL", "WARNING"]
    nli_class: Literal["ENTAILMENT", "NEUTRAL", "CONTRADICTION"]
    risk_rule: Optional[str]
    confidence: float
    matched_fhir_ref: Optional[str]
    detected_at: str


class SpanOut(Strict):
    span_id: str
    note_id: str
    doc_char_start: int
    doc_char_end: int
    classification: Literal["SOURCE_SUPPORTED", "SOURCE_CONTRADICTED", "NO_SOURCE_EVIDENCE", "NON_CLINICAL"]
    claim_id: Optional[str]
    result_id: Optional[str]
    explanation: str


class AuditReport(Strict):
    report_id: str
    note_id: str
    patient_pseudo_id: str
    overall_risk_severity: Severity
    pipeline_status: Literal["VERIFIED", "BLOCKED"]
    counts: dict[str, int]
    hallucination_events: list[EventOut]
    attribution_spans: list[SpanOut]
    trace_id: str
    created_at: str
    idempotent_replay: bool


class NoteClaimIndex(Strict):
    claim_id: str
    char_start: int
    char_end: int
    source_sentence_start: int
    source_sentence_end: int


class NoteView(Strict):
    note_id: str
    patient_pseudo_id: str
    encounter_pseudo_id: Optional[str]
    source_system: str
    note_type: str
    note_sha256: str
    char_length: int
    claim_count: int
    pipeline_status: str
    received_at: Optional[str]
    completed_at: Optional[str]
    claims: list[NoteClaimIndex]


class RedactedSpan(Strict):
    doc_char_start: int
    doc_char_end: int
    risk_severity: Severity
    claim_id: Optional[str]
    reason: str


class InterceptionEvent(Strict):
    event_id: Optional[str]
    claim_id: str
    category: Category
    risk_severity: Literal["CRITICAL", "WARNING"]
    risk_rule: Optional[str]


class Interception(Strict):
    status: Literal["RELEASED", "REDACTED", "BLOCKED"]
    note_id: str
    trace_id: str
    overall_risk_severity: Severity
    redacted_spans: list[RedactedSpan]
    events: list[InterceptionEvent]
    warnings: list[WarningObject]
    claims: list[ClaimOut]
    results: list[VerificationResult]
    reason: Optional[str] = None


class ChatChoiceMessage(Strict):
    role: Literal["assistant"]
    content: Optional[str]


class ChatChoice(Strict):
    index: int
    message: ChatChoiceMessage
    finish_reason: str


class ChatCompletion(Strict):
    id: str
    object: Literal["chat.completion"]
    created: int
    model: str
    choices: list[ChatChoice]
    usage: dict
    interception: Interception
