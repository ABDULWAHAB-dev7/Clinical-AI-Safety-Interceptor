"""The pipeline writing to PostgreSQL. Skipped unless INTERCEPTOR_TEST_DSN is set (see tests/db/test_postgres.py).

Drops and recreates the public schema of the DSN's database.
"""

import os
import unittest

import psycopg

from interceptor.core.pipeline import verify_document
from interceptor.core.policy import load_policy
from interceptor.store import postgres as db
from tests.support import FHIR_DIR, POLICY_PATH, load_example

DSN = os.environ.get("INTERCEPTOR_TEST_DSN")
KEY = b"end-to-end-test-key-0123456789"
POLICY = load_policy(POLICY_PATH)


def verify(example, store):
    doc = load_example(example)
    return verify_document(doc["document_text"], doc["doc_type"], "ENC-SYN-1", policy=POLICY, fhir_dir=FHIR_DIR,
                           store=store, pseudo_key=KEY)


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

    def test_clean_document_is_verified_with_six_traces_and_no_events(self):
        result = verify("verify_clean.json", self.store)
        self.assertTrue(result.delivered)
        status = self.scalar("SELECT pipeline_status FROM intercepted_notes ORDER BY received_at DESC LIMIT 1")
        self.assertEqual(status, "VERIFIED")
        note_id = self.scalar("SELECT note_id FROM intercepted_notes ORDER BY received_at DESC LIMIT 1")
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_traces WHERE note_id = %s", (note_id,)), 6)
        self.assertEqual(self.scalar("SELECT count(*) FROM hallucination_events WHERE note_id = %s", (note_id,)), 0)

    def test_contradicted_document_is_blocked_and_events_are_stored(self):
        result = verify("verify_contradicted.json", self.store)
        self.assertFalse(result.delivered)
        note = self.scalar(
            "SELECT note_id FROM intercepted_notes WHERE pipeline_status = 'BLOCKED' ORDER BY received_at DESC LIMIT 1")
        events = self.scalar("SELECT count(*) FROM hallucination_events WHERE note_id = %s", (note,))
        self.assertEqual(events, 2)
        critical = self.scalar(
            "SELECT count(*) FROM hallucination_events WHERE note_id = %s AND risk_severity = 'CRITICAL'", (note,))
        self.assertEqual(critical, 1)

    def test_events_belong_to_the_verify_trace(self):
        verify("verify_contradicted.json", self.store)
        orphans = self.scalar(
            """SELECT count(*) FROM hallucination_events e
               JOIN audit_traces t ON t.trace_id = e.trace_id
               WHERE t.stage <> 'VERIFY'""")
        self.assertEqual(orphans, 0)

    def test_same_text_again_adds_traces_to_the_same_note(self):
        verify("verify_clean.json", self.store)
        verify("verify_clean.json", self.store)
        self.assertEqual(self.scalar("SELECT count(*) FROM intercepted_notes"), 1)
        note = self.scalar("SELECT note_id FROM intercepted_notes")
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_traces WHERE note_id = %s", (note,)), 12)

    def test_stored_rows_hold_no_document_text(self):
        verify("verify_contradicted.json", self.store)
        raw = self.scalar("SELECT coalesce(string_agg(output_payload::text, ' '), '') FROM audit_traces")
        for fragment in ("metformin", "Hgb", "Allergies", "not on"):
            self.assertNotIn(fragment, raw)

    def test_audit_traces_stay_append_only_after_pipeline_writes(self):
        verify("verify_clean.json", self.store)
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(DSN) as conn:
                conn.execute("UPDATE audit_traces SET status = 'ERROR', error_message = 'x'")


if __name__ == "__main__":
    unittest.main()
