"""Failure injection for Journey D and the fail-closed rules (architecture 7.2, tests/fail_mode)."""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from interceptor.api.app import create_app
from interceptor.core import pipeline
from interceptor.core.errors import ErrorClass, PipelineError
from interceptor.core.models import Outcome, Verdict
from interceptor.core.pipeline import verify_document
from interceptor.core.policy import load_policy
from interceptor.store.records import MemoryAuditStore
from tests.support import FHIR_DIR, POLICY_PATH, load_example

KEY = b"fail-mode-test-key-0123456789"
CLEAN = load_example("verify_clean.json")["document_text"]
NOT_IN_RECORD = "Started on lisinopril 10 mg daily."
PATIENT = {"resourceType": "Patient", "id": "pat-0001",
           "identifier": [{"system": "urn:synthetic:mrn", "value": "SYNTH-MRN-0001"}]}


class _Raises:
    def score(self, premise, hypothesis):
        raise RuntimeError("inference failed")


class _Neutral:
    def score(self, premise, hypothesis):
        return {"entailment": 0.2, "neutral": 0.7, "contradiction": 0.1}


class FailClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.policy = load_policy(POLICY_PATH)
        self.store = MemoryAuditStore()

    def tearDown(self):
        self.tmp.cleanup()

    def run_doc(self, text=CLEAN, encounter="ENC-SYN-1", fhir_dir=FHIR_DIR, store=None, nli_model=None):
        return verify_document(
            text,
            "soap_note",
            encounter,
            policy=self.policy,
            fhir_dir=fhir_dir,
            store=store if store is not None else self.store,
            pseudo_key=KEY,
            nli_model=nli_model,
        )

    def assert_blocked(self, result):
        self.assertEqual(result.outcome, Outcome.HARD_BLOCK)
        self.assertFalse(result.delivered)
        self.assertEqual(result.spans, [])

    def write_bundle(self, name, entries):
        fhir = self.dir / "fhir"
        fhir.mkdir(exist_ok=True)
        (fhir / f"{name}.json").write_text(json.dumps({"resourceType": "Bundle", "entry": entries}), encoding="utf-8")
        return fhir

    def test_unhandled_exception_blocks_whole_document(self):
        with mock.patch.object(pipeline, "decompose", side_effect=RuntimeError("bug")):
            result = self.run_doc()
        self.assert_blocked(result)
        self.assertIn(ErrorClass.UNHANDLED_EXCEPTION.value, result.errors)

    def test_decomposition_error_blocks(self):
        error = PipelineError(ErrorClass.DECOMPOSITION_SCHEMA_INVALID, "bad output")
        with mock.patch.object(pipeline, "decompose", side_effect=error):
            result = self.run_doc()
        self.assert_blocked(result)
        self.assertIn(ErrorClass.DECOMPOSITION_SCHEMA_INVALID.value, result.errors)

    def test_missing_encounter_bundle_blocks(self):
        result = self.run_doc(encounter="ENC-NOPE")
        self.assert_blocked(result)
        self.assertIn(ErrorClass.RETRIEVAL_UNAVAILABLE.value, result.errors)

    def test_path_traversal_encounter_ref_is_rejected(self):
        result = self.run_doc(encounter="../fixtures/fhir/ENC-SYN-1")
        self.assert_blocked(result)
        self.assertIn(ErrorClass.RETRIEVAL_UNAVAILABLE.value, result.errors)

    def test_audit_write_failure_withholds_delivery_even_when_clean(self):
        store = MemoryAuditStore(fail=True)
        result = self.run_doc(store=store)
        self.assert_blocked(result)
        self.assertIn(ErrorClass.AUDIT_WRITE_FAILURE.value, result.errors)
        self.assertEqual(store.requests, [])

    def test_nli_unavailable_becomes_verification_error_and_blocks(self):
        result = self.run_doc(text=NOT_IN_RECORD)
        self.assert_blocked(result)
        self.assertEqual(result.records[0].verdict, Verdict.VERIFICATION_ERROR)
        self.assertEqual(result.records[0].error_class, ErrorClass.NLI_UNAVAILABLE.value)

    def test_failing_nli_model_blocks(self):
        result = self.run_doc(text=NOT_IN_RECORD, nli_model=_Raises())
        self.assert_blocked(result)
        self.assertEqual(result.records[0].error_class, ErrorClass.NLI_UNAVAILABLE.value)

    def test_ungrounded_via_nli_warns_and_delivers(self):
        result = self.run_doc(text=NOT_IN_RECORD, nli_model=_Neutral())
        self.assertEqual(result.outcome, Outcome.WARN)
        self.assertTrue(result.delivered)
        self.assertEqual(len(result.spans), 1)

    def test_malformed_fhir_resource_blocks_with_parse_failure(self):
        fhir = self.write_bundle("ENC-BAD", [
            {"resource": PATIENT},
            {"resource": {"resourceType": "AllergyIntolerance", "code": {"text": "penicillin"}}},
            {"resource": {"resourceType": "MedicationRequest", "id": "mr-1", "medicationCodeableConcept": {"text": "metformin"}}},
            {"resource": {"resourceType": "Observation", "id": "obs-1", "code": {"text": "Hgb"}, "valueQuantity": {"value": 12.1, "unit": "g/dL"}}},
        ])
        result = self.run_doc(encounter="ENC-BAD", fhir_dir=fhir)
        self.assert_blocked(result)
        self.assertIn(ErrorClass.FHIR_PARSE_FAILURE.value, result.errors)

    def test_missing_resource_type_makes_dependent_assertions_errors(self):
        fhir = self.write_bundle("ENC-PART", [
            {"resource": PATIENT},
            {"resource": {"resourceType": "AllergyIntolerance", "id": "al-1", "code": {"text": "penicillin"}}},
            {"resource": {"resourceType": "MedicationRequest", "id": "mr-1", "medicationCodeableConcept": {"text": "metformin"}}},
        ])
        result = self.run_doc(text="Labs: Hgb 12.1 g/dL.", encounter="ENC-PART", fhir_dir=fhir)
        self.assert_blocked(result)
        self.assertEqual(result.records[0].error_class, ErrorClass.RETRIEVAL_PARTIAL.value)

    def test_missing_patient_identifier_blocks_and_nothing_is_recorded(self):
        # The note row needs a patient pseudo-ID, so without one the request cannot be recorded at all.
        fhir = self.write_bundle("ENC-NOPAT", [
            {"resource": {"resourceType": "AllergyIntolerance", "id": "al-1", "code": {"text": "penicillin"}}},
        ])
        result = self.run_doc(encounter="ENC-NOPAT", fhir_dir=fhir)
        self.assert_blocked(result)
        self.assertIn(ErrorClass.RETRIEVAL_PARTIAL.value, result.errors)
        self.assertEqual(self.store.requests, [])

    def test_policy_missing_refuses_to_start(self):
        with self.assertRaises(PipelineError) as ctx:
            create_app(policy_path=self.dir / "missing.yaml", fhir_dir=FHIR_DIR, store=MemoryAuditStore(),
                       pseudo_key=KEY)
        self.assertEqual(ctx.exception.error_class, ErrorClass.POLICY_LOAD_FAILURE)

    def test_short_pseudonymization_key_refuses_to_start(self):
        with self.assertRaises(ValueError):
            create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=b"short")


if __name__ == "__main__":
    unittest.main()
