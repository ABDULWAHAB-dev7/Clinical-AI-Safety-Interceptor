"""The legacy full-pipeline endpoint, POST /v1/verify, now behind bearer auth (API.md 2.1).

Error responses use the API.md envelope; validation failures are 400 VALIDATION_FAILED.
"""

import unittest

from fastapi.testclient import TestClient

from interceptor.api.app import create_app
from interceptor.api.auth import JwtVerifier
from interceptor.store.records import MemoryAuditStore
from tests.support import (
    FHIR_DIR,
    POLICY_PATH,
    PUBLIC_PEM,
    auth_headers,
    load_example,
    make_token,
    mtls_headers,
    security,
)

KEY = b"api-test-key-0123456789abcdef"


class _DownStore(MemoryAuditStore):
    def ping(self):
        return False


def client(store=None):
    app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=store or MemoryAuditStore(),
                     pseudo_key=KEY, verifier=JwtVerifier(PUBLIC_PEM), **security())
    return TestClient(app)


class LegacyVerifyTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryAuditStore()
        self.client = client(self.store)

    def post(self, payload, **kwargs):
        return self.client.post("/v1/verify", json=payload, headers=auth_headers(), **kwargs)

    def test_requires_a_bearer_token(self):
        r = self.client.post("/v1/verify", json=load_example("verify_clean.json"), headers=mtls_headers())
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"]["code"], "AUTH_TOKEN_INVALID")

    def test_clean_document_is_delivered_with_spans(self):
        r = self.post(load_example("verify_clean.json"))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["outcome"], "PASS_THROUGH")
        self.assertEqual(body["pipeline_status"], "VERIFIED")
        self.assertTrue(body["delivered"])
        self.assertEqual(len(body["spans"]), 3)
        self.assertTrue(all(s["classification"] == "SOURCE_SUPPORTED" for s in body["spans"]))

    def test_contradicted_document_is_not_delivered_and_has_no_text(self):
        body = self.post(load_example("verify_contradicted.json")).json()
        self.assertEqual(body["outcome"], "HARD_BLOCK")
        self.assertEqual(body["pipeline_status"], "BLOCKED")
        self.assertFalse(body["delivered"])
        self.assertNotIn("spans", body)
        self.assertIn("drug_allergy_conflict", body["rules_fired"])

    def test_out_of_scope_document_type_is_refused(self):
        r = self.post(load_example("verify_clean.json") | {"doc_type": "dental_note"})
        self.assertEqual(r.status_code, 400)

    def test_oversized_document_is_rejected_not_truncated(self):
        r = self.post(load_example("verify_clean.json") | {"document_text": "Labs: Hgb 12.1 g/dL. " * 600})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "VALIDATION_FAILED")

    def test_missing_encounter_reference_is_rejected(self):
        payload = {k: v for k, v in load_example("verify_clean.json").items() if k != "encounter_ref"}
        r = self.post(payload)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["param"], "encounter_ref")

    def test_unknown_field_is_rejected(self):
        r = self.post(load_example("verify_clean.json") | {"policy_override": "pass"})
        self.assertEqual(r.status_code, 400)

    def test_every_request_is_recorded_and_holds_no_document_text(self):
        self.post(load_example("verify_clean.json"))
        self.post(load_example("verify_contradicted.json"))
        self.assertEqual(len(self.store.notes), 2)
        serialized = repr([list(self.store.notes.values()), self.store.traces, self.store.events])
        for fragment in ("drugs allergies", "HbA1c was 8.2", "type 2 diabetes", "Started amoxicillin", "denies known"):
            self.assertNotIn(fragment, serialized)


class HealthAndContractTests(unittest.TestCase):
    def test_health_is_public(self):
        self.assertEqual(client().get("/v1/health").json(), {"status": "ok"})

    def test_readiness_reports_database_state(self):
        self.assertEqual(client().get("/v1/health/ready").status_code, 200)
        down = client(_DownStore())
        r = down.get("/v1/health/ready")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["components"]["database"], "unreachable")

    def test_readiness_says_what_the_verifier_can_do(self):
        """A model that cannot be reached is reported, not claimed.

        The interceptor stays up when the NLI service is down: the claims that need the model fail closed and the
        rest of the service keeps working. The readiness report has to say that, rather than claim a model that is
        not there.
        """
        body = client().get("/v1/health/ready").json()
        self.assertIn(body["components"]["verifier"], ("deterministic_only", "nli_available", "nli_unavailable"))
        self.assertEqual(body["ready"], True)

    def test_old_unversioned_health_paths_are_gone(self):
        self.assertEqual(client().get("/healthz").status_code, 404)

    def test_expired_legacy_token_is_refused(self):
        r = client().post("/v1/verify", json=load_example("verify_clean.json"),
                          headers={"Authorization": f"Bearer {make_token(issued_at=1_000_000)}"} | mtls_headers())
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
