"""PostgreSQL repository for the audit schema (DATABASE.md section 3).

Writes go through explicit methods that take a connection, so a request's rows can be written in one transaction.
The repository never updates or deletes audit rows; the database triggers reject those anyway.
"""


import threading

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from interceptor.core.errors import ErrorClass, PipelineError
from interceptor.paths import PROJECT_ROOT
from interceptor.store.records import IDEMPOTENCY_TTL_SECONDS, AuditRefs, NoteRegistration, OverrideRefused, RecordRefs

SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"


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


_POOLS: dict[tuple[str, float, int], ConnectionPool] = {}  # one pool per (DSN, connect timeout), shared by every store in the process
_POOLS_LOCK = threading.Lock()


def _shared_pool(dsn, connect_timeout, pool_size):
    key = (dsn, connect_timeout, pool_size)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None or pool.closed:
            pool = ConnectionPool(dsn, min_size=1, max_size=pool_size, timeout=connect_timeout + 3,
                                  # No automatic prepared statements: a cached plan points at the old table IDs
                                  # after a migration or a schema reset, and every later call on it would fail.
                                  kwargs={"connect_timeout": connect_timeout, "prepare_threshold": None},
                                  # Check a connection before handing it out. A hosted database closes idle
                                  # connections, and the pool cannot tell until one is used: without this the
                                  # first request after an idle period gets a dead connection, fails to write
                                  # its audit row, and blocks a note for the wrong reason. A failed check
                                  # discards the connection and the pool supplies another.
                                  check=ConnectionPool.check_connection, open=True)
            _POOLS[key] = pool
        return pool


class PostgresAuditStore:
    """Writes one request's records in a single transaction. Any database error is AUDIT_WRITE_FAILURE."""

    def __init__(self, dsn, connect_timeout=10, pool_size=4):
        # A pool, not a connection per call: a remote database (for example a hosted one) can cost over a second to
        # connect, and one request makes several store calls. The default connect timeout is ten seconds, not
        # three: a cold function in a cold region can take longer than that to open a connection, and a timeout
        # that is too short turns a healthy database into a reported outage, and a clinical request into a block
        # for the wrong reason.
        self.dsn = dsn
        self.connect_timeout = connect_timeout
        # Shared: many stores (one per app, and one per test) must not each hold their own connections.
        self._pool = _shared_pool(dsn, connect_timeout, pool_size)

    def _connect(self):
        # Used as `with self._connect() as conn:`, which commits on success and rolls back on any exception,
        # as psycopg.connect did.
        return self._pool.connection()


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
                trace_ids = {}
                for st in request.stages:
                    trace_ids[st.stage] = _add_stage(conn, note_id, request.patient_pseudo_id, st)
                event_ids = {}
                for ev in request.events:
                    event_ids[ev.claim_id] = _add_event_record(
                        conn, trace_ids.get("VERIFY"), note_id, request.patient_pseudo_id, ev)[0]
                return RecordRefs(str(note_id), {k: str(v) for k, v in trace_ids.items()},
                                  {k: str(v) for k, v in event_ids.items()})
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)


    def register_note(self, *, patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256,
                      char_length, extract):
        """Decompose path. A new note is stored as RECEIVED with one EXTRACT trace, in one transaction.

        A duplicate (same patient and text) returns the existing note_id and writes nothing (DATABASE.md uniqueness).
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    INSERT INTO intercepted_notes
                        (patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256,
                         char_length, pipeline_status, claim_count)
                    VALUES (%s, %s, %s, %s, %s, %s, 'RECEIVED', %s)
                    ON CONFLICT (patient_pseudo_id, note_sha256) DO NOTHING
                    RETURNING note_id
                    """,
                    (patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256, char_length,
                     extract.output_payload.get("assertion_count", 0)),
                ).fetchone()
                if row is None:
                    existing = conn.execute(
                        "SELECT note_id FROM intercepted_notes WHERE patient_pseudo_id = %s AND note_sha256 = %s",
                        (patient_pseudo_id, note_sha256),
                    ).fetchone()
                    latest = conn.execute(
                        "SELECT trace_id FROM audit_traces WHERE note_id = %s AND stage = 'EXTRACT' "
                        "ORDER BY created_at DESC LIMIT 1",
                        (existing[0],),
                    ).fetchone()
                    return NoteRegistration(str(existing[0]), False, str(latest[0]) if latest else None)
                note_id = row[0]
                trace = _add_stage(conn, note_id, patient_pseudo_id, extract)
                return NoteRegistration(str(note_id), True, str(trace))
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)

    def note_patient(self, note_id):
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT patient_pseudo_id FROM intercepted_notes WHERE note_id = %s", (note_id,)
                ).fetchone()
                return row[0] if row else None
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)

    def record_verify(self, note_id, stage):
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT patient_pseudo_id FROM intercepted_notes WHERE note_id = %s", (note_id,)
                ).fetchone()
                if row is None:
                    raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, "note disappeared")
                return str(_add_stage(conn, note_id, row[0], stage))
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)

    # ---- idempotency, reads, and audit reports (no schema changes: payloads hold hashes and codes only) ----
    def find_idempotency(self, scope, ttl_seconds=IDEMPOTENCY_TTL_SECONDS):
        """Looks the key up in trace payloads by JSON containment (the GIN index on output_payload serves this)."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT output_payload, stage, note_id, created_at FROM audit_traces
                    WHERE output_payload @> %s AND created_at > now() - make_interval(secs => %s)
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (Jsonb({"idempotency": {"scope": scope}}), ttl_seconds),
                ).fetchone()
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)
        if row is None:
            return None
        payload = row[0]
        return {"body_sha256": payload["idempotency"]["body_sha256"], "stage": row[1], "note_id": str(row[2]),
                "report_id": payload.get("report_id"), "created_at": row[3]}

    def get_note(self, note_id):
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT note_id, patient_pseudo_id, encounter_pseudo_id, source_system, note_type, note_sha256,
                           char_length, claim_count, pipeline_status, received_at, completed_at
                    FROM intercepted_notes WHERE note_id = %s
                    """,
                    (note_id,),
                ).fetchone()
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)
        if row is None:
            return None
        keys = ("note_id", "patient_pseudo_id", "encounter_pseudo_id", "source_system", "note_type", "note_sha256",
                "char_length", "claim_count", "pipeline_status", "received_at", "completed_at")
        note = dict(zip(keys, row))
        note["note_id"] = str(note["note_id"])
        note["note_sha256"] = note["note_sha256"].strip()
        return note

    def get_claim_index(self, note_id):
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT output_payload -> 'claims' FROM audit_traces WHERE note_id = %s AND stage = 'EXTRACT' "
                    "AND output_payload ? 'claims' ORDER BY created_at DESC LIMIT 1",
                    (note_id,),
                ).fetchone()
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)
        return row[0] if row else None

    def get_trace(self, trace_id):
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT stage, note_id FROM audit_traces WHERE trace_id = %s", (trace_id,)
                ).fetchone()
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)
        return {"trace_id": str(trace_id), "stage": row[0], "note_id": str(row[1])} if row else None

    def write_audit_report(self, *, note_id, stage, events, pipeline_status, claim_count):
        """One transaction: the ATTRIBUTE trace, its events, and the note status. Any failure rolls all of it back."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT patient_pseudo_id FROM intercepted_notes WHERE note_id = %s FOR UPDATE", (note_id,)
                ).fetchone()
                if row is None:
                    raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, "note disappeared")
                patient = row[0]
                trace = conn.execute(
                    """
                    INSERT INTO audit_traces
                        (note_id, patient_pseudo_id, stage, component_name, component_version, input_sha256,
                         output_payload, status, error_message, latency_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING trace_id, created_at
                    """,
                    (note_id, patient, stage.stage, stage.component_name, stage.component_version,
                     stage.input_sha256, Jsonb(stage.output_payload), stage.status, stage.error_message,
                     stage.latency_ms),
                ).fetchone()
                created = tuple(
                    (e.claim_id, *_add_event_record(conn, trace[0], note_id, patient, e)) for e in events
                )
                conn.execute(
                    "UPDATE intercepted_notes SET pipeline_status = %s, claim_count = %s, completed_at = now() "
                    "WHERE note_id = %s",
                    (pipeline_status, claim_count, note_id),
                )
                return AuditRefs(str(trace[0]), trace[1], tuple((c, str(i), d) for c, i, d in created))
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)

    def get_report(self, report_id):
        try:
            with self._connect() as conn:
                trace = conn.execute(
                    "SELECT trace_id, note_id, patient_pseudo_id, output_payload, created_at FROM audit_traces "
                    "WHERE stage = 'ATTRIBUTE' AND output_payload @> %s ORDER BY created_at LIMIT 1",
                    (Jsonb({"report_id": report_id}),),
                ).fetchone()
                if trace is None:
                    return None
                rows = conn.execute(
                    """
                    SELECT event_id, claim_id, category, risk_severity, nli_class, risk_rule, confidence,
                           matched_fhir_ref, detected_at
                    FROM hallucination_events WHERE trace_id = %s ORDER BY claim_id
                    """,
                    (trace[0],),
                ).fetchall()
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)
        keys = ("event_id", "claim_id", "category", "risk_severity", "nli_class", "risk_rule", "confidence",
                "matched_fhir_ref", "detected_at")
        events = []
        for row in rows:
            event = dict(zip(keys, row))
            event["event_id"] = str(event["event_id"])
            event["confidence"] = float(event["confidence"])
            events.append(event)
        return {"trace_id": str(trace[0]), "note_id": str(trace[1]), "patient_pseudo_id": trace[2],
                "payload": trace[3], "created_at": trace[4], "events": events}

    # ---- streaming: each sentence is audited before it is released ----
    def stream_begin(self, *, patient_pseudo_id, encounter_pseudo_id, source_system, note_type, placeholder_sha):
        try:
            with self._connect() as conn:
                return str(upsert_note(
                    conn, patient_pseudo_id=patient_pseudo_id, encounter_pseudo_id=encounter_pseudo_id,
                    source_system=source_system, note_type=note_type, note_sha256=placeholder_sha, char_length=0,
                    pipeline_status="RECEIVED", claim_count=0))
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)

    def stream_sentence(self, *, note_id, stage, events):
        try:
            with self._connect() as conn:
                patient = conn.execute(
                    "SELECT patient_pseudo_id FROM intercepted_notes WHERE note_id = %s", (note_id,)
                ).fetchone()[0]
                trace_id = _add_stage(conn, note_id, patient, stage)
                ids = {e.claim_id: str(_add_event_record(conn, trace_id, note_id, patient, e)[0]) for e in events}
                return str(trace_id), ids
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)

    def mark_note_failed(self, note_id):
        try:
            with self._connect() as conn:
                conn.execute("UPDATE intercepted_notes SET pipeline_status = 'FAILED', completed_at = now() "
                             "WHERE note_id = %s", (note_id,))
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)

    def list_notes_by_status(self, status, limit=100):
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT note_id, patient_pseudo_id, encounter_pseudo_id, source_system, note_type, claim_count,
                           pipeline_status, received_at, completed_at,
                           (SELECT t.output_payload->>'report_id' FROM audit_traces t
                            WHERE t.note_id = n.note_id AND t.stage = 'ATTRIBUTE'
                            ORDER BY t.created_at DESC LIMIT 1) AS report_id
                    FROM intercepted_notes n WHERE pipeline_status = %s ORDER BY received_at DESC LIMIT %s
                    """,
                    (status, limit),
                ).fetchall()
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)
        keys = ("note_id", "patient_pseudo_id", "encounter_pseudo_id", "source_system", "note_type", "claim_count",
                "pipeline_status", "received_at", "completed_at", "report_id")
        return [dict(zip(keys, r)) | {"note_id": str(r[0])} for r in rows]

    def record_override(self, *, report_id, claim_id, reviewer_ref, comment, note_status=None):
        return self.record_review(report_id=report_id, claim_id=claim_id, resolution="FALSE_POSITIVE",
                                  reviewer_ref=reviewer_ref, comment=comment, note_status=note_status)

    def get_report_reviews(self, report_id):
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT e.event_id, e.claim_id, r.resolution, r.reviewed_at
                    FROM hallucination_events e
                    JOIN audit_traces t ON t.trace_id = e.trace_id
                    JOIN hallucination_reviews r ON r.event_id = e.event_id
                    WHERE t.stage = 'ATTRIBUTE' AND t.output_payload->>'report_id' = %s
                    ORDER BY r.reviewed_at
                    """,
                    (report_id,),
                ).fetchall()
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)
        return [{"event_id": str(r[0]), "claim_id": r[1], "resolution": r[2], "reviewed_at": r[3]} for r in rows]

    def record_review(self, *, report_id, claim_id, resolution, reviewer_ref, comment, note_status=None):
        """One transaction: the review (append-only) with this resolution and, if given, the note status."""
        try:
            with self._connect() as conn:
                found = conn.execute(
                    """
                    SELECT e.event_id, e.note_id FROM hallucination_events e
                    JOIN audit_traces t ON t.trace_id = e.trace_id
                    WHERE t.stage = 'ATTRIBUTE' AND t.output_payload->>'report_id' = %s AND e.claim_id = %s
                    """,
                    (report_id, claim_id),
                ).fetchone()
                if found is None:
                    raise OverrideRefused("no finding for this span in the report")
                event_id, note_id = found
                if conn.execute("SELECT 1 FROM hallucination_reviews WHERE event_id = %s LIMIT 1", (event_id,)).fetchone():
                    raise OverrideRefused("this finding was already reviewed")
                review = conn.execute(
                    """
                    INSERT INTO hallucination_reviews (event_id, resolution, reviewer_ref, comment)
                    VALUES (%s, %s, %s, %s) RETURNING review_id, reviewed_at
                    """,
                    (event_id, resolution, reviewer_ref, comment),
                ).fetchone()
                if note_status is not None:
                    conn.execute("UPDATE intercepted_notes SET pipeline_status = %s WHERE note_id = %s",
                                 (note_status, note_id))
                return {"review_id": str(review[0]), "event_id": str(event_id), "note_id": str(note_id),
                        "reviewed_at": review[1]}
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)

    def stream_finish(self, *, note_id, note_sha256, char_length, claim_count, pipeline_status, stage):
        """Sets the real hash unless the same text was already audited for this patient, then status and final trace."""
        try:
            with self._connect() as conn:
                patient = conn.execute(
                    "SELECT patient_pseudo_id FROM intercepted_notes WHERE note_id = %s", (note_id,)
                ).fetchone()[0]
                conn.execute(
                    """
                    UPDATE intercepted_notes SET note_sha256 = %s
                    WHERE note_id = %s AND NOT EXISTS (
                        SELECT 1 FROM intercepted_notes other
                        WHERE other.patient_pseudo_id = %s AND other.note_sha256 = %s)
                    """,
                    (note_sha256, note_id, patient, note_sha256),
                )
                conn.execute(
                    "UPDATE intercepted_notes SET char_length = %s, claim_count = %s, pipeline_status = %s, "
                    "completed_at = now() WHERE note_id = %s",
                    (char_length, claim_count, pipeline_status, note_id),
                )
                return str(_add_stage(conn, note_id, patient, stage))
        except psycopg.Error as exc:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, type(exc).__name__)


def _add_event_record(conn, trace_id, note_id, patient_pseudo_id, ev):
    """Inserts one hallucination_events row. Returns (event_id, detected_at)."""
    return conn.execute(
        """
        INSERT INTO hallucination_events
            (trace_id, note_id, patient_pseudo_id, claim_id, category, risk_severity, nli_class, risk_rule,
             confidence, matched_fhir_ref, claim_text, doc_char_start, doc_char_end)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING event_id, detected_at
        """,
        (trace_id, note_id, patient_pseudo_id, ev.claim_id, ev.category, ev.risk_severity, ev.nli_class,
         ev.risk_rule, ev.confidence, ev.matched_fhir_ref, ev.claim_text, ev.doc_char_start, ev.doc_char_end),
    ).fetchone()


def _add_stage(conn, note_id, patient_pseudo_id, stage):
    return add_trace(
        conn,
        note_id=note_id,
        patient_pseudo_id=patient_pseudo_id,
        stage=stage.stage,
        component_name=stage.component_name,
        component_version=stage.component_version,
        input_sha256=stage.input_sha256,
        status=stage.status,
        output_payload=stage.output_payload,
        error_message=stage.error_message,
        latency_ms=stage.latency_ms,
    )
