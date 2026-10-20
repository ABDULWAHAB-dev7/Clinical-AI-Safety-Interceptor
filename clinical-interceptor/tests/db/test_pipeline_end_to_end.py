"""The pipeline writing to PostgreSQL. Skipped unless INTERCEPTOR_TEST_DSN is set (see tests/db/test_postgres.py).

Drops and recreates the public schema of the DSN's database.
"""

import os
import unittest

import psycopg
from fastapi.testclient import TestClient

from interceptor.api.app import create_app
from interceptor.api.auth import JwtVerifier
from interceptor.store import postgres as db
from tests.support import FHIR_DIR, POLICY_PATH, PUBLIC_PEM, auth_headers, load_example, security

DSN = os.environ.get("INTERCEPTOR_TEST_DSN")
KEY = b"end-to-end-test-key-0123456789"


def verify(example, store):
    """Runs the document check (POST /v1/verify) against PostgreSQL and returns the response body."""
    app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=store, pseudo_key=KEY,
                     verifier=JwtVerifier(PUBLIC_PEM), **security())
    response = TestClient(app).post("/v1/verify", json=load_example(example), headers=auth_headers())
    assert response.status_code == 200, response.text
    return response.json()


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class PipelineEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        with psycopg.connect(DSN) as conn:
            db.apply_schema(conn)
        cls.store = db.PostgresAuditStore(DSN)

    def setUp(self):
        # TRUNCATE does not fire the row-level append-only triggers, so each test starts empty.
        with psycopg.connect(DSN) as conn:
            conn.execute("TRUNCATE hallucination_reviews, hallucination_events, audit_traces, intercepted_notes CASCADE")

    def scalar(self, sql, params=()):
        with psycopg.connect(DSN) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def test_clean_document_is_verified_with_three_traces_and_no_events(self):
        result = verify("verify_clean.json", self.store)
        self.assertTrue(result["delivered"])
        status = self.scalar("SELECT pipeline_status FROM intercepted_notes ORDER BY received_at DESC LIMIT 1")
        self.assertEqual(status, "VERIFIED")
        note_id = self.scalar("SELECT note_id FROM intercepted_notes ORDER BY received_at DESC LIMIT 1")
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_traces WHERE note_id = %s", (note_id,)), 3)
        self.assertEqual(self.scalar("SELECT count(*) FROM hallucination_events WHERE note_id = %s", (note_id,)), 0)

    def test_contradicted_document_is_blocked_and_events_are_stored(self):
        result = verify("verify_contradicted.json", self.store)
        self.assertFalse(result["delivered"])
        note = self.scalar(
            "SELECT note_id FROM intercepted_notes WHERE pipeline_status = 'BLOCKED' ORDER BY received_at DESC LIMIT 1")
        events = self.scalar("SELECT count(*) FROM hallucination_events WHERE note_id = %s", (note,))
        self.assertEqual(events, 3)  # the denial, the amoxicillin order and the HbA1c mismatch
        critical = self.scalar(
            "SELECT count(*) FROM hallucination_events WHERE note_id = %s AND risk_severity = 'CRITICAL'", (note,))
        self.assertEqual(critical, 2)  # the denial and the amoxicillin order

    def test_events_belong_to_the_report_trace(self):
        # Events are written with the audit report (ATTRIBUTE), as in the /v1/interceptor audit path.
        verify("verify_contradicted.json", self.store)
        orphans = self.scalar(
            """SELECT count(*) FROM hallucination_events e
               JOIN audit_traces t ON t.trace_id = e.trace_id
               WHERE t.stage <> 'ATTRIBUTE'""")
        self.assertEqual(orphans, 0)

    def test_same_text_again_adds_traces_to_the_same_note(self):
        verify("verify_clean.json", self.store)
        verify("verify_clean.json", self.store)
        self.assertEqual(self.scalar("SELECT count(*) FROM intercepted_notes"), 1)
        note = self.scalar("SELECT note_id FROM intercepted_notes")
        # EXTRACT is written once per note; the second request adds VERIFY and ATTRIBUTE only.
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_traces WHERE note_id = %s", (note,)), 5)

    def test_stored_rows_hold_no_document_text(self):
        verify("verify_contradicted.json", self.store)
        raw = self.scalar("SELECT coalesce(string_agg(output_payload::text, ' '), '') FROM audit_traces")
        for fragment in ("denies known", "Started amoxicillin", "HbA1c was 6.1"):
            self.assertNotIn(fragment, raw)

    def test_audit_traces_stay_append_only_after_pipeline_writes(self):
        verify("verify_clean.json", self.store)
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(DSN) as conn:
                conn.execute("UPDATE audit_traces SET status = 'ERROR', error_message = 'x'")


if __name__ == "__main__":
    unittest.main()
