"""The proxy against PostgreSQL: the streaming tests rerun on a real store, plus the non-streaming audit rows.

Skipped unless INTERCEPTOR_TEST_DSN is set. Drops and recreates the public schema of the DSN's database.
"""

import hashlib
import os
import unittest

import psycopg

from interceptor.store import postgres as db
from tests.support import auth_headers
from tests.support_proxy import OUTPUT, chat_body, make_app
from tests.unit import test_proxy_stream as streams

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
class PgSpecStreamTests(PgMixin, streams.SpecStreamTests):
    pass


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class PgStreamAuditTests(PgMixin, streams.StreamAuditTests):
    pass


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class PgStreamFailClosedTests(PgMixin, streams.StreamFailClosedTests):
    pass


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class PgNonStreamingTests(PgMixin, unittest.TestCase):
    def setUp(self):
        self.store = self.make_store()
        self.client, self.upstream, _ = make_app(store=self.store)

    def rows(self, sql):
        with psycopg.connect(DSN) as conn:
            return conn.execute(sql).fetchall()

    def test_one_request_writes_a_note_six_traces_and_the_events(self):
        r = self.client.post("/v1/chat/completions/intercept", json=chat_body(), headers=auth_headers())
        self.assertEqual(r.status_code, 200)
        note = self.rows("SELECT pipeline_status, claim_count, note_sha256 FROM intercepted_notes")[0]
        self.assertEqual((note[0], note[1], note[2].strip()), ("RELEASED", 3, hashlib.sha256(OUTPUT.encode()).hexdigest()))
        stages = [row[0] for row in self.rows("SELECT stage FROM audit_traces")]
        self.assertEqual(sorted(stages), sorted(["INGEST", "RETRIEVE", "EXTRACT", "VERIFY", "ATTRIBUTE", "INTERCEPT"]))
        events = self.rows("SELECT category, risk_severity FROM hallucination_events")
        self.assertEqual(sorted(events), [("DRUG_ALLERGY_CONFLICT", "CRITICAL")] * 2)
        trace = r.json()["interception"]["trace_id"]
        self.assertEqual(self.rows(f"SELECT stage FROM audit_traces WHERE trace_id = '{trace}'")[0][0], "INTERCEPT")  # noqa: S608

    def test_event_ids_in_the_response_exist_in_the_database(self):
        r = self.client.post("/v1/chat/completions/intercept", json=chat_body(), headers=auth_headers())
        ids = {e["event_id"] for e in r.json()["interception"]["events"]}
        stored = {str(row[0]) for row in self.rows("SELECT event_id FROM hallucination_events")}
        self.assertEqual(ids, stored)

    def test_stored_rows_hold_no_model_output(self):
        self.client.post("/v1/chat/completions/intercept", json=chat_body(), headers=auth_headers())
        text = " ".join(r[0] for r in self.rows("SELECT output_payload::text FROM audit_traces"))
        text += " ".join(r[0] for r in self.rows("SELECT row_to_json(e)::text FROM hallucination_events e"))
        for fragment in ("denies", "amoxicillin", "sinusitis"):
            self.assertNotIn(fragment, text)

    def test_the_same_output_twice_updates_one_note_and_keeps_both_audits(self):
        for _ in range(2):
            self.client.post("/v1/chat/completions/intercept", json=chat_body(), headers=auth_headers())
        self.assertEqual(self.rows("SELECT count(*) FROM intercepted_notes")[0][0], 1)
        self.assertEqual(self.rows("SELECT count(*) FROM audit_traces WHERE stage = 'INTERCEPT'")[0][0], 2)

    def test_unreachable_database_blocks_the_response(self):
        self.client.app.state.store = db.PostgresAuditStore("host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1")
        r = self.client.post("/v1/chat/completions/intercept", json=chat_body(), headers=auth_headers())
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "AUDIT_WRITE_FAILED"))
        self.assertNotIn("choices", r.json())

    def test_stream_with_an_unreachable_database_never_starts(self):
        self.client.app.state.store = db.PostgresAuditStore("host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1")
        r = self.client.post("/v1/chat/completions/intercept", json=chat_body(stream=True), headers=auth_headers())
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "AUDIT_WRITE_FAILED"))
        self.assertTrue(r.headers["content-type"].startswith("application/json"))


if __name__ == "__main__":
    unittest.main()
