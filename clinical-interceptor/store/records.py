"""What one request writes to the audit store, and the in-memory store used by tests.

A request produces one note row, one audit_traces row per stage, and one hallucination_events row per claim whose
severity is CRITICAL or WARNING. Records hold hashes, codes, counts and pseudo-IDs only, never note text.
"""

from dataclasses import dataclass, field
from typing import Optional

from core.errors import ErrorClass, PipelineError


@dataclass(frozen=True)
class StageRecord:
    stage: str  # INGEST | RETRIEVE | EXTRACT | VERIFY | ATTRIBUTE | INTERCEPT (the pipeline_stage enum)
    component_name: str
    component_version: str
    input_sha256: str
    output_payload: dict
    status: str  # OK | ERROR | SKIPPED
    error_message: Optional[str] = None
    latency_ms: Optional[int] = None


@dataclass(frozen=True)
class EventRecord:
    """Belongs to the VERIFY stage trace of the same request."""

    claim_id: str
    category: str
    risk_severity: str  # CRITICAL | WARNING
    nli_class: str  # ENTAILMENT | NEUTRAL | CONTRADICTION
    confidence: float
    claim_text: str  # tokenized descriptor; draft text is not stored (DATABASE.md open item)
    risk_rule: Optional[str]
    matched_fhir_ref: Optional[str]
    doc_char_start: int
    doc_char_end: int


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    patient_pseudo_id: str
    encounter_pseudo_id: str
    source_system: str
    note_type: str
    note_sha256: str
    char_length: int
    claim_count: int
    pipeline_status: str  # VERIFIED | BLOCKED
    stages: tuple = field(default_factory=tuple)
    events: tuple = field(default_factory=tuple)


class MemoryAuditStore:
    """Holds records in memory. `fail=True` simulates an audit write failure (fail-closed tests)."""

    def __init__(self, fail=False):
        self.fail = fail
        self.requests = []

    def ping(self):
        return True

    def record(self, request):
        if self.fail:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, "injected failure")
        self.requests.append(request)
