"""POST /v1/verify, the single-call document check. It runs the same engine as the /v1/interceptor endpoints, so these
tests check that the verdicts match and that every failure fails closed (architecture 7.2).
"""

import json
import pathlib
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient

from interceptor.api.app import create_app
from interceptor.api.auth import JwtVerifier
from interceptor.core.pseudo import patient_pseudo_id
from interceptor.store.records import MemoryAuditStore
from tests.support import FHIR_DIR, POLICY_PATH, PUBLIC_PEM, auth_headers, load_example, security

KEY = b"document-check-test-key-0123456789"
PATIENT = patient_pseudo_id("SYNTH-MRN-0001", KEY)
RAW_BUNDLE = json.loads((FHIR_DIR / "ENC-SYN-1.json").read_text(encoding="utf-8"))
CLEAN = load_example("verify_clean.json")
CONTRADICTED = load_example("verify_contradicted.json")
NEEDS_NLI = "Blood pressure is stable."


class _Neutral:
    def score(self, premise, hypothesis):
        return {"entailment": 0.2, "neutral": 0.7, "contradiction": 0.1}


def app_client(store=None, fhir_dir=FHIR_DIR, nli_model=None):
    store = store if store is not None else MemoryAuditStore()
    app = create_app(policy_path=POLICY_PATH, fhir_dir=fhir_dir, store=store, pseudo_key=KEY,
                     verifier=JwtVerifier(PUBLIC_PEM), nli_model=nli_model, **security())
    return TestClient(app), store


def verdict_set(verdicts):
    return sorted((v["claim_id"], v["nli_class"], v["risk_severity"], v["risk_rule"], v["matched_fhir_ref"])
                  for v in verdicts)


class DocumentCheckTests(unittest.TestCase):
    def setUp(self):
        self.client, self.store = app_client()

    def post(self, payload, client=None):
        return (client or self.client).post("/v1/verify", json=payload, headers=auth_headers())

    def test_clean_document_is_released_with_spans_and_three_traces(self):
        r = self.post(CLEAN)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["outcome"], body["pipeline_status"], body["delivered"]),
                         ("PASS_THROUGH", "VERIFIED", True))
        self.assertEqual(len(body["spans"]), 3)
        self.assertEqual(sorted(t["stage"] for t in self.store.traces), ["ATTRIBUTE", "EXTRACT", "VERIFY"])

    def test_contradicted_document_is_blocked_with_events_and_no_text(self):
        body = self.post(CONTRADICTED).json()
        self.assertEqual((body["outcome"], body["pipeline_status"], body["delivered"]),
                         ("HARD_BLOCK", "BLOCKED", False))
        self.assertNotIn("spans", body)
        self.assertIn("drug_allergy_conflict", body["rules_fired"])
        categories = {e["category"] for e in self.store.events}
        self.assertIn("DRUG_ALLERGY_CONFLICT", categories)

    def test_same_verdicts_as_the_three_interceptor_endpoints(self):
        """The document check and the decompose, verify, audit flow must agree on every claim."""
        legacy = verdict_set(self.post(CONTRADICTED).json()["verdicts"])

        decomposed = self.client.post(
            "/v1/interceptor/decompose",
            json={"patient_pseudo_id": PATIENT, "note": {"source_system": "BENCH", "note_type": "soap_note",
                                                         "text": CONTRADICTED["document_text"]}},
            headers=auth_headers() | {"Idempotency-Key": str(uuid.uuid4())}).json()
        claims = decomposed["claims"]
        verified = self.client.post(
            "/v1/interceptor/verify",
            json={"patient_pseudo_id": PATIENT, "note_id": decomposed["note_id"], "fhir_bundle": RAW_BUNDLE,
                  "claims": [{k: c[k] for k in ("claim_id", "claim_text", "char_start", "char_end", "entities")}
                             for c in claims]},
            headers=auth_headers()).json()
        self.assertEqual(legacy, verdict_set(verified["results"]))

    def test_missing_encounter_bundle_is_refused_and_nothing_is_released(self):
        r = self.post(CLEAN | {"encounter_ref": "ENC-NOPE"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "INTERCEPT_BUNDLE_INVALID")
        self.assertEqual(self.store.notes, {})

    def test_path_traversal_in_the_encounter_reference_is_rejected(self):
        r = self.post(CLEAN | {"encounter_ref": "../fixtures/fhir/ENC-SYN-1"})
        self.assertEqual(r.status_code, 400)

    def test_audit_write_failure_withholds_delivery_even_when_clean(self):
        client, _ = app_client(store=MemoryAuditStore(fail=True))
        r = self.post(CLEAN, client=client)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "AUDIT_WRITE_FAILED")
        self.assertNotIn("spans", r.text)

    def test_statement_needing_the_nli_model_fails_closed_without_one(self):
        r = self.post(CLEAN | {"document_text": NEEDS_NLI})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "INTERCEPT_VERIFIER_UNAVAILABLE")
        self.assertNotIn("ATTRIBUTE", [t["stage"] for t in self.store.traces])

    def test_statement_needing_the_nli_model_warns_and_delivers_with_a_neutral_model(self):
        client, _ = app_client(nli_model=_Neutral())
        body = self.post(CLEAN | {"document_text": NEEDS_NLI}, client=client).json()
        self.assertEqual((body["outcome"], body["delivered"]), ("WARN", True))
        self.assertEqual(len(body["spans"]), 1)

    def test_malformed_fhir_resource_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = pathlib.Path(tmp) / "ENC-BAD.json"
            bad.write_text(json.dumps({"resourceType": "Bundle", "type": "collection", "entry": [
                {"resource": RAW_BUNDLE["entry"][0]["resource"]},
                {"resource": {"resourceType": "AllergyIntolerance", "code": {"text": "penicillin"}}},
            ]}), encoding="utf-8")
            client, store = app_client(fhir_dir=pathlib.Path(tmp))
            r = self.post(CLEAN | {"encounter_ref": "ENC-BAD"}, client=client)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "INTERCEPT_BUNDLE_INVALID")
        self.assertEqual(store.notes, {})

    def test_bundle_without_a_patient_identifier_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = json.loads(json.dumps(RAW_BUNDLE))
            bundle["entry"][0]["resource"].pop("identifier")
            (pathlib.Path(tmp) / "ENC-NOID.json").write_text(json.dumps(bundle), encoding="utf-8")
            client, store = app_client(fhir_dir=pathlib.Path(tmp))
            r = self.post(CLEAN | {"encounter_ref": "ENC-NOID"}, client=client)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(store.notes, {})


class StartUpTests(unittest.TestCase):
    def test_policy_missing_refuses_to_start(self):
        from interceptor.core.errors import PipelineError

        with self.assertRaises(PipelineError):
            create_app(policy_path="missing.yaml", fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=KEY)

    def test_short_pseudonymization_key_refuses_to_start(self):
        with self.assertRaises(ValueError):
            create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=b"short")


if __name__ == "__main__":
    unittest.main()
