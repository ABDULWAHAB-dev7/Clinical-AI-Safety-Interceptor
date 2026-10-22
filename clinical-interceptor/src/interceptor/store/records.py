"""What requests write to the audit store, and the in-memory store used by tests.

A request produces one note row, audit_traces rows (one per stage), and one hallucination_events row per claim whose
severity is CRITICAL or WARNING. Records hold hashes, codes, counts and pseudo-IDs only, never note text.

PostgresAuditStore (store/postgres.py) and MemoryAuditStore have the same methods:
  ping, record, register_note, note_patient, record_verify,
  find_idempotency, get_note, get_claim_index, get_trace, write_audit_report, get_report
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional

from interceptor.core.errors import ErrorClass, PipelineError

IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60  # API.md 8.3


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
    """Belongs to the VERIFY stage trace of a document request, or the ATTRIBUTE trace of an audit report."""

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


@dataclass(frozen=True)
class RecordRefs:
    """IDs created by record(): the note, one trace per stage, and one event per claim."""

    note_id: str
    trace_ids: dict  # stage -> trace_id
    event_ids: dict  # claim_id -> event_id
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class AuditRefs:
    """IDs created by write_audit_report()."""

    trace_id: str
    created_at: datetime
    events: tuple  # (claim_id, event_id, detected_at)


class OverrideRefused(Exception):
    """A clinician override cannot be recorded: no such finding, or it was already reviewed."""


class MemoryAuditStore:
    """Holds records in memory. `fail=True` simulates an audit write failure (fail-closed tests)."""

    def __init__(self, fail=False, clock=None):
        self.fail = fail
        self.clock = clock or (lambda: datetime.now(UTC))
        self.requests = []
        self.notes = {}  # (patient_pseudo_id, note_sha256) -> note dict
        self.traces = []  # dicts: note_id, patient, stage, trace_id, payload, created_at
        self.events = []  # dicts: trace_id, note_id, event_id, claim_id, ..., detected_at
        self.reviews = []  # append-only review decisions (hallucination_reviews): event_id, resolution, reviewer_ref

    def ping(self):
        return True

    def _check(self):
        if self.fail:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, "injected failure")

    # ---- document pipeline and proxy ---------------------------------------------------------------
    def record(self, request):
        self._check()
        self.requests.append(request)
        note = self._upsert_note(request.patient_pseudo_id, request.encounter_pseudo_id, request.source_system,
                                 request.note_type, request.note_sha256, request.char_length,
                                 request.pipeline_status, request.claim_count)
        trace_ids = {}
        for stage in request.stages:
            trace_ids[stage.stage] = self._add_trace(note["note_id"], request.patient_pseudo_id, stage.stage, stage)
        event_ids = {}
        verify_trace = trace_ids.get("VERIFY")
        for event in request.events:
            event_ids[event.claim_id] = self._add_event(verify_trace, note["note_id"], request.patient_pseudo_id, event)
        return RecordRefs(note["note_id"], trace_ids, event_ids, self.clock())

    # ---- decompose and verify endpoints ------------------------------------------------------------
    def register_note(self, *, patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256,
                      char_length, extract):
        """A new note gets one EXTRACT trace. A duplicate returns the existing note and writes nothing."""
        self._check()
        key = (patient_pseudo_id, note_sha256)
        if key in self.notes:
            existing = self.notes[key]
            latest = [t["trace_id"] for t in self.traces
                      if t["note_id"] == existing["note_id"] and t["stage"] == "EXTRACT"]
            return NoteRegistration(existing["note_id"], False, latest[-1] if latest else None)
        note = self._upsert_note(patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256,
                                 char_length, "RECEIVED", extract.output_payload.get("assertion_count", 0))
        trace_id = self._add_trace(note["note_id"], patient_pseudo_id, "EXTRACT", extract)
        return NoteRegistration(note["note_id"], True, trace_id)

    def note_patient(self, note_id):
        note = self._note_by_id(note_id)
        return note["patient_pseudo_id"] if note else None

    def record_verify(self, note_id, stage):
        self._check()
        note = self._note_by_id(note_id)
        return self._add_trace(note_id, note["patient_pseudo_id"] if note else None, "VERIFY", stage)

    # ---- idempotency, reads, and audit reports -----------------------------------------------------
    def find_idempotency(self, scope, ttl_seconds=IDEMPOTENCY_TTL_SECONDS):
        cutoff = self.clock() - timedelta(seconds=ttl_seconds)
        for trace in reversed(self.traces):
            marker = trace["payload"].get("idempotency")
            if marker and marker.get("scope") == scope and trace["created_at"] >= cutoff:
                return {"body_sha256": marker["body_sha256"], "stage": trace["stage"], "note_id": trace["note_id"],
                        "report_id": trace["payload"].get("report_id"), "created_at": trace["created_at"]}
        return None

    def get_note(self, note_id):
        note = self._note_by_id(note_id)
        return dict(note) if note else None

    def get_claim_index(self, note_id):
        for trace in reversed(self.traces):
            if trace["note_id"] == note_id and trace["stage"] == "EXTRACT" and "claims" in trace["payload"]:
                return list(trace["payload"]["claims"])
        return None

    def get_trace(self, trace_id):
        for trace in self.traces:
            if trace["trace_id"] == trace_id:
                return {"trace_id": trace_id, "stage": trace["stage"], "note_id": trace["note_id"]}
        return None

    def write_audit_report(self, *, note_id, stage, events, pipeline_status, claim_count):
        self._check()
        note = self._note_by_id(note_id)
        if note is None:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, "note disappeared")
        trace_id = self._add_trace(note_id, note["patient_pseudo_id"], "ATTRIBUTE", stage)
        created = [self._add_event(trace_id, note_id, note["patient_pseudo_id"], e) for e in events]
        note["pipeline_status"], note["claim_count"], note["completed_at"] = pipeline_status, claim_count, self.clock()
        trace = next(t for t in self.traces if t["trace_id"] == trace_id)
        refs = tuple((e.claim_id, event_id, self._event(event_id)["detected_at"]) for e, event_id in zip(events, created))
        return AuditRefs(trace_id, trace["created_at"], refs)

    def get_report(self, report_id):
        for trace in self.traces:
            if trace["stage"] == "ATTRIBUTE" and trace["payload"].get("report_id") == report_id:
                rows = [dict(e) for e in self.events if e["trace_id"] == trace["trace_id"]]
                return {"trace_id": trace["trace_id"], "note_id": trace["note_id"],
                        "patient_pseudo_id": trace["patient"], "payload": trace["payload"],
                        "created_at": trace["created_at"], "events": sorted(rows, key=lambda r: r["claim_id"])}
        return None

    # ---- streaming: each sentence is audited before it is released ---------------------------------
    def stream_begin(self, *, patient_pseudo_id, encounter_pseudo_id, source_system, note_type, placeholder_sha):
        """Creates the note before any content is released. The real hash is unknown until the stream ends."""
        self._check()
        note = self._upsert_note(patient_pseudo_id, encounter_pseudo_id, source_system, note_type, placeholder_sha,
                                 0, "RECEIVED", 0)
        return note["note_id"]

    def stream_sentence(self, *, note_id, stage, events):
        """One trace and its events for one sentence. Returns (trace_id, {claim_id: event_id})."""
        self._check()
        note = self._note_by_id(note_id)
        trace_id = self._add_trace(note_id, note["patient_pseudo_id"], stage.stage, stage)
        ids = {e.claim_id: self._add_event(trace_id, note_id, note["patient_pseudo_id"], e) for e in events}
        return trace_id, ids

    def mark_note_failed(self, note_id):
        """The note could not be verified. It stays visible as FAILED until it is checked again."""
        self._check()
        note = self._note_by_id(note_id)
        if note is not None:
            note["pipeline_status"], note["status"], note["completed_at"] = "FAILED", "FAILED", self.clock()

    def list_notes_by_status(self, status, limit=100):
        """Notes with this pipeline status, newest first, without any content."""
        self._check()
        rows = [n for n in self.notes.values() if n["pipeline_status"] == status]
        rows.sort(key=lambda n: n["received_at"], reverse=True)
        keys = ("note_id", "patient_pseudo_id", "encounter_pseudo_id", "source_system", "note_type", "claim_count",
                "pipeline_status", "received_at", "completed_at")
        return [{k: n[k] for k in keys} for n in rows[:limit]]

    def record_override(self, *, report_id, claim_id, reviewer_ref, comment, note_status=None):
        """Appends a FALSE_POSITIVE review to the event for this claim, and optionally sets the note status."""
        return self.record_review(report_id=report_id, claim_id=claim_id, resolution="FALSE_POSITIVE",
                                  reviewer_ref=reviewer_ref, comment=comment, note_status=note_status)

    def get_report_reviews(self, report_id):
        """Every review of a finding in this report: event_id, claim_id, resolution, reviewed_at."""
        self._check()
        report = self.get_report(report_id)
        if report is None:
            return []
        claim_of = {e["event_id"]: e["claim_id"] for e in report["events"]}
        return [{"event_id": r["event_id"], "claim_id": claim_of[r["event_id"]], "resolution": r["resolution"],
                 "reviewed_at": r["reviewed_at"]} for r in self.reviews if r["event_id"] in claim_of]

    def record_review(self, *, report_id, claim_id, resolution, reviewer_ref, comment, note_status=None):
        """Appends one review with the given resolution to the event for this claim, and optionally sets the note status."""
        self._check()
        report = self.get_report(report_id)
        event = None if report is None else next((e for e in report["events"] if e["claim_id"] == claim_id), None)
        if event is None:
            raise OverrideRefused("no finding for this span in the report")
        if any(r["event_id"] == event["event_id"] for r in self.reviews):
            raise OverrideRefused("this finding was already reviewed")
        review = {"review_id": str(uuid.uuid4()), "event_id": event["event_id"], "resolution": resolution,
                  "reviewer_ref": reviewer_ref, "comment": comment, "reviewed_at": self.clock()}
        self.reviews.append(review)
        if note_status is not None:
            self._note_by_id(report["note_id"])["pipeline_status"] = note_status
        return {"review_id": review["review_id"], "event_id": event["event_id"],
                "note_id": report["note_id"], "reviewed_at": review["reviewed_at"]}

    def stream_finish(self, *, note_id, note_sha256, char_length, claim_count, pipeline_status, stage):
        """Sets the real hash (unless the same text was already audited for this patient), status, and final trace."""
        self._check()
        note = self._note_by_id(note_id)
        old_key = (note["patient_pseudo_id"], note["note_sha256"])
        new_key = (note["patient_pseudo_id"], note_sha256)
        if new_key not in self.notes:
            self.notes.pop(old_key, None)
            note["note_sha256"] = note_sha256
            self.notes[new_key] = note
        note["char_length"], note["claim_count"] = char_length, claim_count
        note["pipeline_status"], note["status"], note["completed_at"] = pipeline_status, pipeline_status, self.clock()
        return self._add_trace(note_id, note["patient_pseudo_id"], stage.stage, stage)

    # ---- internals ---------------------------------------------------------------------------------
    def _note_by_id(self, note_id):
        return next((n for n in self.notes.values() if n["note_id"] == note_id), None)

    def _upsert_note(self, patient, encounter, source_system, note_type, sha, char_length, status, claim_count):
        key = (patient, sha)
        now = self.clock()
        if key in self.notes:
            note = self.notes[key]
            note["pipeline_status"], note["claim_count"], note["completed_at"] = status, claim_count, now
            return note
        note = {
            "note_id": str(uuid.uuid4()), "patient_pseudo_id": patient, "encounter_pseudo_id": encounter,
            "source_system": source_system, "note_type": note_type, "note_sha256": sha, "char_length": char_length,
            "claim_count": claim_count, "pipeline_status": status, "received_at": now, "completed_at": None,
            "patient": patient,  # kept for existing tests
            "status": status,
        }
        self.notes[key] = note
        return note

    def _add_trace(self, note_id, patient, stage_name, row):
        trace_id = str(uuid.uuid4())
        self.traces.append({"note_id": note_id, "patient": patient, "stage": stage_name, "trace_id": trace_id,
                            "payload": row.output_payload, "created_at": self.clock()})
        return trace_id

    def _add_event(self, trace_id, note_id, patient, event):
        event_id = str(uuid.uuid4())
        self.events.append({
            "event_id": event_id, "trace_id": trace_id, "note_id": note_id, "patient_pseudo_id": patient,
            "claim_id": event.claim_id, "category": event.category, "risk_severity": event.risk_severity,
            "nli_class": event.nli_class, "risk_rule": event.risk_rule, "confidence": event.confidence,
            "matched_fhir_ref": event.matched_fhir_ref, "doc_char_start": event.doc_char_start,
            "doc_char_end": event.doc_char_end, "detected_at": self.clock(),
        })
        return event_id

    def _event(self, event_id):
        return next(e for e in self.events if e["event_id"] == event_id)
