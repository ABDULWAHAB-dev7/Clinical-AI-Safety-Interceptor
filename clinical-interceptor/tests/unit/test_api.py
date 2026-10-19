import pathlib
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import create_app
from store.audit import AuditLog
from tests.support import FHIR_DIR, POLICY_PATH, load_example


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_path = pathlib.Path(self.tmp.name) / "audit.jsonl"
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, audit_path=self.audit_path)
        self.client = TestClient(app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_document_is_delivered_with_spans(self):
        r = self.client.post("/v1/verify", json=load_example("verify_clean.json"))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["outcome"], "PASS_THROUGH")
        self.assertTrue(body["delivered"])
        self.assertEqual(len(body["spans"]), 3)
        self.assertTrue(all(s["verdict"] == "SUPPORTED" for s in body["spans"]))

    def test_contradicted_document_is_not_delivered_and_has_no_text(self):
        r = self.client.post("/v1/verify", json=load_example("verify_contradicted.json"))
        body = r.json()
        self.assertEqual(body["outcome"], "HARD_BLOCK")
        self.assertFalse(body["delivered"])
        self.assertNotIn("spans", body)
        self.assertIn("R3_contradiction", body["rules_fired"])

    def test_out_of_scope_document_type_is_refused(self):
        payload = load_example("verify_clean.json") | {"doc_type": "dental_note"}
        self.assertEqual(self.client.post("/v1/verify", json=payload).status_code, 400)

    def test_oversized_document_is_rejected_not_truncated(self):
        payload = load_example("verify_clean.json") | {"document_text": "Labs: Hgb 12.1 g/dL. " * 600}
        self.assertEqual(self.client.post("/v1/verify", json=payload).status_code, 422)

    def test_missing_encounter_reference_is_rejected(self):
        payload = {k: v for k, v in load_example("verify_clean.json").items() if k != "encounter_ref"}
        self.assertEqual(self.client.post("/v1/verify", json=payload).status_code, 422)

    def test_unknown_field_is_rejected(self):
        payload = load_example("verify_clean.json") | {"policy_override": "pass"}
        self.assertEqual(self.client.post("/v1/verify", json=payload).status_code, 422)

    def test_audit_chain_intact_and_holds_no_document_text(self):
        self.client.post("/v1/verify", json=load_example("verify_clean.json"))
        self.client.post("/v1/verify", json=load_example("verify_contradicted.json"))
        self.assertEqual(AuditLog(self.audit_path).verify(), (True, None))
        raw = self.audit_path.read_text(encoding="utf-8")
        for fragment in ("metformin", "twice daily", "Hgb 12.1", "Allergies:"):
            self.assertNotIn(fragment, raw)

    def test_health_and_readiness(self):
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        ready = self.client.get("/readyz").json()
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["nli_model"], "not_loaded")


if __name__ == "__main__":
    unittest.main()
