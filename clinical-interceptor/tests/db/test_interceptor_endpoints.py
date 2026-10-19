"""decompose and verify over HTTP, writing to PostgreSQL. Skipped unless INTERCEPTOR_TEST_DSN is set.

Drops and recreates the public schema of the DSN's database.
"""

import os
import unittest
import uuid

import psycopg
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import JwtVerifier
from store import postgres as db
from tests.support import security, mtls_headers, POLICY_PATH, FHIR_DIR, PUBLIC_PEM, auth_headers
from tests.support_bundle import BUNDLE
from tests.unit.test_interceptor_api import KEY, PID, decompose_body

DSN = os.environ.get("INTERCEPTOR_TEST_DSN")


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class InterceptorEndpointDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        with psycopg.connect(DSN) as conn:
            db.apply_schema(conn)
        cls.store = db.PostgresAuditStore(DSN)
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=cls.store, pseudo_key=KEY,
                         verifier=JwtVerifier(PUBLIC_PEM), **security())
        cls.client = TestClient(app)

    def setUp(self):
        with psycopg.connect(DSN) as conn:
            conn.execute("TRUNCATE hallucination_reviews, hallucination_events, audit_traces, intercepted_notes CASCADE")

    def scalar(self, sql, params=()):
        with psycopg.connect(DSN) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def decompose(self):
        return self.client.post("/v1/interceptor/decompose", json=decompose_body(),
                                headers={"Idempotency-Key": str(uuid.uuid4())} | auth_headers())

    def test_new_note_is_received_with_one_extract_trace(self):
        r = self.decompose()
        self.assertEqual(r.status_code, 201)
        note_id = r.json()["note_id"]
        self.assertEqual(self.scalar("SELECT pipeline_status FROM intercepted_notes WHERE note_id = %s", (note_id,)),
                         "RECEIVED")
        self.assertEqual(self.scalar(
            "SELECT count(*) FROM audit_traces WHERE note_id = %s AND stage = 'EXTRACT'", (note_id,)), 1)
        self.assertEqual(r.json()["decompose_trace_id"], str(self.scalar(
            "SELECT trace_id FROM audit_traces WHERE note_id = %s AND stage = 'EXTRACT'", (note_id,))))

    def test_duplicate_note_returns_existing_id_and_writes_nothing_new(self):
        first = self.decompose().json()
        second = self.decompose()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["note_id"], first["note_id"])
        self.assertEqual(self.scalar("SELECT count(*) FROM audit_traces"), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM intercepted_notes"), 1)

    def test_note_stores_no_text(self):
        self.decompose()
        stored = self.scalar("SELECT row_to_json(n)::text FROM intercepted_notes n")
        self.assertNotIn("Patient denies", stored)
        self.assertNotIn("HbA1c", stored)

    def test_verify_with_note_writes_a_verify_trace(self):
        note_id = self.decompose().json()["note_id"]
        body = {
            "patient_pseudo_id": PID,
            "note_id": note_id,
            "fhir_bundle": BUNDLE,
            "claims": [{"claim_id": "clm_000000000001", "claim_text": "Patient has type 2 diabetes",
                        "char_start": 0, "char_end": 27}],
        }
        r = self.client.post("/v1/interceptor/verify", json=body, headers=auth_headers())
        self.assertEqual(r.status_code, 200, r.text)
        trace = r.json()["verify_trace_id"]
        self.assertEqual(self.scalar("SELECT stage FROM audit_traces WHERE trace_id = %s", (trace,)), "VERIFY")
        self.assertEqual(self.scalar("SELECT patient_pseudo_id FROM audit_traces WHERE trace_id = %s", (trace,)), PID)

    def test_audit_traces_stay_append_only_for_endpoint_writes(self):
        self.decompose()
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(DSN) as conn:
                conn.execute("DELETE FROM audit_traces")


if __name__ == "__main__":
    unittest.main()
