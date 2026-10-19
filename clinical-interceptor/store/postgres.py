"""PostgreSQL repository for the audit schema (DATABASE.md section 3).

Writes go through explicit methods that take a connection, so a request's rows can be written in one transaction.
The repository never updates or deletes audit rows; the database triggers reject those anyway.
"""

import pathlib

import psycopg
from psycopg.types.json import Jsonb

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
