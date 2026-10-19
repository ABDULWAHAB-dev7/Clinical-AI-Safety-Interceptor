"""What one request writes to the audit store, and the in-memory store used by tests.

A request produces one note row, one audit_traces row per stage, and one hallucination_events row per claim whose
severity is CRITICAL or WARNING. Records hold hashes, codes, counts and pseudo-IDs only, never note text.
"""

import uuid
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


@dataclass(frozen=True)
class NoteRegistration:
    note_id: str
    created: bool
    decompose_trace_id: Optional[str]


class MemoryAuditStore:
    """Holds records in memory. `fail=True` simulates an audit write failure (fail-closed tests)."""

    def __init__(self, fail=False):
        self.fail = fail
        self.requests = []
        self.notes = {}  # (patient_pseudo_id, note_sha256) -> {"note_id", "status", "claim_count", "patient"}
        self.traces = []  # dicts: note_id, stage, trace_id, payload

    def ping(self):
        return True

    def _check(self):
        if self.fail:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, "injected failure")

    def record(self, request):
        self._check()
        self.requests.append(request)

    def register_note(self, *, patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256,
                      char_length, extract):
        """Same contract as PostgresAuditStore.register_note: a new note gets one EXTRACT trace."""
        self._check()
        key = (patient_pseudo_id, note_sha256)
        if key in self.notes:
            existing = self.notes[key]
            latest = [t["trace_id"] for t in self.traces if t["note_id"] == existing["note_id"] and t["stage"] == "EXTRACT"]
            return NoteRegistration(existing["note_id"], False, latest[-1] if latest else None)
        note_id = str(uuid.uuid4())
        self.notes[key] = {"note_id": note_id, "status": "RECEIVED", "claim_count": extract.output_payload.get("assertion_count", 0),
                           "patient": patient_pseudo_id}
        trace_id = self._add_trace(note_id, "EXTRACT", extract)
        return NoteRegistration(note_id, True, trace_id)

    def note_patient(self, note_id):
        for note in self.notes.values():
            if note["note_id"] == note_id:
                return note["patient"]
        return None

    def record_verify(self, note_id, stage):
        self._check()
        return self._add_trace(note_id, "VERIFY", stage)

    def _add_trace(self, note_id, stage, row):
        trace_id = str(uuid.uuid4())
        self.traces.append({"note_id": note_id, "stage": stage, "trace_id": trace_id, "payload": row.output_payload})
        return trace_id
