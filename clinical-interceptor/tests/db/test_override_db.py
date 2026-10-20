"""The clinician override on PostgreSQL. Skipped unless INTERCEPTOR_TEST_DSN is set."""

import os
import unittest

import psycopg
from fastapi.testclient import TestClient

from interceptor.api.app import create_app
from interceptor.api.auth import JwtVerifier
from interceptor.store import postgres as db
from tests.support import FHIR_DIR, POLICY_PATH, PUBLIC_PEM, auth_headers, load_example, security

DSN = os.environ.get("INTERCEPTOR_TEST_DSN")
KEY = b"override-db-test-key-0123456789ab"


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class OverrideDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        with psycopg.connect(DSN) as conn:
            db.apply_schema(conn)
        cls.store = db.PostgresAuditStore(DSN)

    def scalar(self, sql, params=()):
        with psycopg.connect(DSN) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def test_override_appends_one_review_and_moves_the_note_to_overridden(self):
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=self.store, pseudo_key=KEY,
                         verifier=JwtVerifier(PUBLIC_PEM), **security())
        client = TestClient(app)
        blocked = client.post("/v1/verify", json=load_example("verify_contradicted.json"),
                              headers=auth_headers()).json()
        self.assertEqual(blocked["pipeline_status"], "BLOCKED")
        report = client.get(f"/v1/interceptor/audit/reports/{blocked['request_id']}", headers=auth_headers()).json()
        critical = next(s for s in report["attribution_spans"]
                        if any(e["claim_id"] == s["claim_id"] and e["risk_severity"] == "CRITICAL"
                               for e in report["hallucination_events"]))
        r = client.post("/v1/interceptor/override", headers=auth_headers(), json={
            "report_id": blocked["request_id"], "span_id": critical["span_id"],
            "override_reason": "Verbal order in exam room", "attending_signature": "Dr. Example Attending"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.scalar("SELECT pipeline_status FROM intercepted_notes WHERE note_id = %s",
                                     (blocked["note_id"],)), "OVERRIDDEN_BY_CLINICIAN")
        self.assertEqual(self.scalar("SELECT count(*) FROM hallucination_reviews WHERE resolution = 'FALSE_POSITIVE'"), 1)
        # the second attempt is refused and nothing is written
        again = client.post("/v1/interceptor/override", headers=auth_headers(), json={
            "report_id": blocked["request_id"], "span_id": critical["span_id"],
            "override_reason": "Verbal order in exam room", "attending_signature": "Dr. Example Attending"})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(self.scalar("SELECT count(*) FROM hallucination_reviews"), 1)

    def test_reviews_stay_append_only(self):
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(DSN) as conn:
                conn.execute("UPDATE hallucination_reviews SET comment = 'changed'")


if __name__ == "__main__":
    unittest.main()
