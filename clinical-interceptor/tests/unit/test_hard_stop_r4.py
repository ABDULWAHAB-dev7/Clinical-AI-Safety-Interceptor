"""Hard-stop R4 on the live request path (docs/policy-changes/2026-10-22-hard-stop-r4.md).

A document that yields no claims has not been verified. It must be blocked, not passed through as clean.
"""

import unittest

from fastapi.testclient import TestClient

from interceptor.api.app import create_app
from interceptor.api.auth import JwtVerifier
from interceptor.core.policy import R4
from interceptor.store.records import MemoryAuditStore
from tests.support import FHIR_DIR, POLICY_PATH, PUBLIC_PEM, auth_headers, load_example, security

KEY = b"r4-test-key-0123456789abcdef"
EMPTY_TEXTS = ["...", "!!!", "?"]  # punctuation only: the decomposer finds no sentence


class HardStopR4Tests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryAuditStore()
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=self.store, pseudo_key=KEY,
                         verifier=JwtVerifier(PUBLIC_PEM), **security())
        self.client = TestClient(app)

    def check(self, text):
        payload = load_example("verify_clean.json") | {"document_text": text}
        return self.client.post("/v1/verify", json=payload, headers=auth_headers())

    def test_a_document_with_no_claims_is_blocked_and_not_delivered(self):
        for text in EMPTY_TEXTS:
            with self.subTest(text=text):
                response = self.check(text)
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["outcome"], "HARD_BLOCK")
                self.assertFalse(body["delivered"])
                self.assertEqual(body["pipeline_status"], "BLOCKED")
                self.assertIn(R4, body["rules_fired"])
                self.assertNotIn("spans", body)  # a blocked document returns no text and no spans

    def test_the_stored_report_names_the_rule(self):
        self.check("...")
        report = next(t["payload"] for t in self.store.traces if t["stage"] == "ATTRIBUTE")
        self.assertEqual(report["hard_stop_rules"], [R4])
        self.assertEqual(report["pipeline_status"], "BLOCKED")

    def test_a_normal_document_is_not_affected(self):
        body = self.check(load_example("verify_clean.json")["document_text"]).json()
        self.assertEqual(body["outcome"], "PASS_THROUGH")
        self.assertNotIn(R4, body["rules_fired"])
        report = next(t["payload"] for t in self.store.traces if t["stage"] == "ATTRIBUTE")
        self.assertEqual(report["hard_stop_rules"], [])


if __name__ == "__main__":
    unittest.main()
