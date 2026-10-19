"""PostgreSQL repository for the audit schema (DATABASE.md section 3).

Writes go through explicit methods that take a connection, so a request's rows can be written in one transaction.
The repository never updates or deletes audit rows; the database triggers reject those anyway.
"""

import pathlib

import psycopg
from psycopg.types.json import Jsonb

from core.errors import ErrorClass, PipelineError

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def connect(dsn):
    return psycopg.connect(dsn)


def apply_schema(conn):
    """Creates the schema. Not idempotent: run against an empty database (or drop the schema first)."""
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def add_note(conn, *, patient_pseudo_id, source_system, note_type, note_sha256, char_length,
             encounter_pseudo_id=None, note_storage_uri=None):
    row = conn.execute(
        """
        INSERT INTO intercepted_notes
            (patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256,
             note_storage_uri, char_length)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING note_id
        """,
        (patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256,
         note_storage_uri, char_length),
    ).fetchone()
    return row[0]


def add_trace(conn, *, note_id, patient_pseudo_id, stage, component_name, component_version, input_sha256,
              status, output_payload=None, error_message=None, latency_ms=None):
    row = conn.execute(
        """
        INSERT INTO audit_traces
            (note_id, patient_pseudo_id, stage, component_name, component_version, input_sha256,
             output_payload, status, error_message, latency_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING trace_id
        """,
        (note_id, patient_pseudo_id, stage, component_name, component_version, input_sha256,
         Jsonb(output_payload or {}), status, error_message, latency_ms),
    ).fetchone()
    return row[0]


def add_event(conn, *, trace_id, note_id, patient_pseudo_id, claim_id, category, risk_severity, nli_class,
              confidence, claim_text, risk_rule=None, matched_fhir_ref=None, doc_char_start=None,
              doc_char_end=None):
    row = conn.execute(
        """
        INSERT INTO hallucination_events
            (trace_id, note_id, patient_pseudo_id, claim_id, category, risk_severity, nli_class, risk_rule,
             confidence, matched_fhir_ref, claim_text, doc_char_start, doc_char_end)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING event_id
        """,
        (trace_id, note_id, patient_pseudo_id, claim_id, category, risk_severity, nli_class, risk_rule,
         confidence, matched_fhir_ref, claim_text, doc_char_start, doc_char_end),
    ).fetchone()
    return row[0]


def add_review(conn, *, event_id, resolution, reviewer_ref, comment=None):
    row = conn.execute(
        """
        INSERT INTO hallucination_reviews (event_id, resolution, reviewer_ref, comment)
        VALUES (%s, %s, %s, %s)
        RETURNING review_id
        """,
        (event_id, resolution, reviewer_ref, comment),
    ).fetchone()
    return row[0]


def current_resolution(conn, event_id):
    row = conn.execute(
        "SELECT resolution FROM v_hallucination_current WHERE event_id = %s", (event_id,)
    ).fetchone()
    return row[0] if row else None


def upsert_note(conn, *, patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256,
                char_length, pipeline_status, claim_count):
    """Inserts the note, or updates it when the same text is verified again for the same patient.

    Notes are not append-only (DATABASE.md 3.2), so updating status and claim count is allowed. Each verification
    adds its own audit_traces rows, so the history is kept.
    """
    row = conn.execute(
        """
        INSERT INTO intercepted_notes
            (patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256, char_length,
             pipeline_status, claim_count, completed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (patient_pseudo_id, note_sha256) DO UPDATE
            SET pipeline_status = EXCLUDED.pipeline_status,
                claim_count     = EXCLUDED.claim_count,
                completed_at    = now()
        RETURNING note_id
        """,
        (patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256, char_length,
         pipeline_status, claim_count),
    ).fetchone()
    return row[0]


class PostgresAuditStore:
    """Writes one request's records in a single transaction. Any database error is AUDIT_WRITE_FAILURE."""

    def __init__(self, dsn, connect_timeout=3):
        self.dsn = dsn
        self.connect_timeout = connect_timeout

    def _connect(self):
        return psycopg.connect(self.dsn, connect_timeout=self.connect_timeout)

    def ping(self):
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1")
            return True
        except psycopg.Error:
            return False

    def record(self, request):
        try:
            with self._connect() as conn:  # commits on success, rolls back on any exception
                note_id = upsert_note(
                    conn,
                    patient_pseudo_id=request.patient_pseudo_id,
                    encounter_pseudo_id=request.encounter_pseudo_id,
                    source_system=request.source_system,
                    note_type=request.note_type,
                    note_sha256=request.note_sha256,
                    char_length=request.char_length,
                    pipeline_status=request.pipeline_status,
                    claim_count=request.claim_count,
                )
                verify_trace = None
                for st in request.stages:
                    trace = add_trace(
                        conn,
                        note_id=note_id,
                        patient_pseudo_id=request.patient_pseudo_id,
                        stage=st.stage,
                        component_name=st.component_name,
                        component_version=st.component_version,
                        input_sha256=st.input_sha256,
                        status=st.status,
                        output_payload=st.output_payload,
                        error_message=st.error_message,
                        latency_ms=st.latency_ms,
                    )
                    if st.stage == "VERIFY":
                        verify_trace = trace
                for ev in request.events:
                    add_event(
                        conn,
                        trace_id=verify_trace,
                        note_id=note_id,
                        patient_pseudo_id=request.patient_pseudo_id,
                        claim_id=ev.claim_id,
                        category=ev.category,
                        risk_severity=ev.risk_severity,
                        nli_class=ev.nli_class,
                        confidence=ev.confidence,
                        claim_text=ev.claim_text,
                        risk_rule=ev.risk_rule,
                        matched_fhir_ref=ev.matched_fhir_ref,
                        doc_char_start=ev.doc_char_start,
                        doc_char_end=ev.doc_char_end,
                    )
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)
