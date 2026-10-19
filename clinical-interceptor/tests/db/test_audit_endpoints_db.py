"""Every audit and read test from tests/unit/test_audit_api.py, rerun against PostgreSQL, plus database-only checks.

Skipped unless INTERCEPTOR_TEST_DSN is set. Drops and recreates the public schema of the DSN's database.
"""

import os
import unittest
import uuid

import psycopg

from interceptor.store import postgres as db
from interceptor.store.records import EventRecord, StageRecord
from tests.unit import test_audit_api as base

DSN = os.environ.get("INTERCEPTOR_TEST_DSN")


def reset():
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    with psycopg.connect(DSN) as conn:
        db.apply_schema(conn)


class PgMixin:
    @classmethod
    def setUpClass(cls):
        reset()

    def make_store(self):
        with psycopg.connect(DSN) as conn:
            conn.execute("TRUNCATE hallucination_reviews, hallucination_events, audit_traces, intercepted_notes CASCADE")
        return db.PostgresAuditStore(DSN)


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class PgAuditEndpointTests(PgMixin, base.AuditEndpointTests):
    pass


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class PgReadEndpointTests(PgMixin, base.ReadEndpointTests):
    pass


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class PgOnlyTests(PgMixin, base.AuditBase):
    def event(self, claim_id):
        return EventRecord(claim_id, "UNSUPPORTED_CLAIM", "WARNING", "NEUTRAL", 0.9, "claim:x",
                           "unsupported_clinical", None, 0, 5)

    def test_report_write_is_atomic_when_an_event_fails(self):
        stage = StageRecord("ATTRIBUTE", "audit-report", "t", "0" * 64, {"report_id": str(uuid.uuid4())}, "OK")
        bad = EventRecord("not-a-claim-id", "UNSUPPORTED_CLAIM", "WARNING", "NEUTRAL", 0.9, "claim:x", None, None, 0, 5)
        with self.assertRaises(Exception):
            self.store.write_audit_report(note_id=self.note_id, stage=stage, events=[bad],
                                          pipeline_status="BLOCKED", claim_count=9)
        with psycopg.connect(DSN) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit_traces WHERE stage = 'ATTRIBUTE'").fetchone()[0], 0)
            status = conn.execute("SELECT pipeline_status FROM intercepted_notes WHERE note_id = %s",
                                  (self.note_id,)).fetchone()[0]
        self.assertEqual(status, "RECEIVED")  # the note update was rolled back with the rest

    def test_idempotency_uses_a_24_hour_window_in_the_database(self):
        key = str(uuid.uuid4())
        self.post(self.body(), key=key)
        scope = base._idempotency_scope("client-test", key)
        self.assertIsNotNone(self.store.find_idempotency(scope))
        self.assertIsNone(self.store.find_idempotency(scope, ttl_seconds=0))

    def test_report_events_are_linked_to_the_attribute_trace(self):
        report = self.post().json()
        with psycopg.connect(DSN) as conn:
            orphans = conn.execute(
                "SELECT count(*) FROM hallucination_events e JOIN audit_traces t ON t.trace_id = e.trace_id "
                "WHERE t.stage <> 'ATTRIBUTE'").fetchone()[0]
            linked = conn.execute("SELECT count(*) FROM hallucination_events WHERE trace_id = %s",
                                  (report["trace_id"],)).fetchone()[0]
        self.assertEqual(orphans, 0)
        self.assertEqual(linked, len(report["hallucination_events"]))

    def test_stored_audit_rows_stay_append_only(self):
        self.post()
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(DSN) as conn:
                conn.execute("DELETE FROM audit_traces WHERE stage = 'ATTRIBUTE'")

    def test_unreachable_database_is_a_503_not_a_crash(self):
        self.store = db.PostgresAuditStore("host=127.0.0.1 port=1 dbname=none user=none connect_timeout=1")
        self.client.app.state.store = self.store
        r = self.post()
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "AUDIT_WRITE_FAILED"))


if __name__ == "__main__":
    unittest.main()
