import unittest

from core.decompose import decompose
from core.errors import ErrorClass, PipelineError
from core.models import Verdict
from fhir.bundle import ContextBundle, Named, Observation
from nli.deterministic import verify
from nli.model import nli_verdict, verdict_from_scores
from core.policy import Policy

BUNDLE = ContextBundle(
    encounter_ref="ENC-T",
    allergies=[Named(id="al-1", name="penicillin")],
    medications=[Named(id="mr-1", name="metformin")],
    observations=[Observation(id="obs-1", name="Hgb", value=12.1, unit="g/dL")],
)
POLICY = Policy("t", frozenset({"soap_note"}), 0.85, 0.85, 0.0)


def verdict_of(text, bundle=BUNDLE, tolerance=0.0):
    assertion = decompose(text, hints=bundle.hints())[0]
    result = verify(assertion, bundle, tolerance)
    return None if result is None else result.verdict


class DeterministicTests(unittest.TestCase):
    def test_allergy_match_supported(self):
        self.assertEqual(verdict_of("Allergies: penicillin."), Verdict.SUPPORTED)

    def test_negated_recorded_allergy_contradicted(self):
        self.assertEqual(verdict_of("No known allergy to penicillin."), Verdict.CONTRADICTED)

    def test_nkda_contradicted_when_record_has_allergies(self):
        self.assertEqual(verdict_of("No known drug allergies."), Verdict.CONTRADICTED)

    def test_no_coded_allergy_match_routes_to_nli(self):
        self.assertIsNone(verdict_of("Allergies: none."))

    def test_medication_supported_and_negated_contradicted(self):
        self.assertEqual(verdict_of("Patient on metformin."), Verdict.SUPPORTED)
        self.assertEqual(verdict_of("Patient is not on metformin."), Verdict.CONTRADICTED)

    def test_unrecorded_medication_routes_to_nli(self):
        self.assertIsNone(verdict_of("Started on lisinopril 10 mg daily."))

    def test_lab_same_value_supported(self):
        self.assertEqual(verdict_of("Labs: Hgb 12.1 g/dL."), Verdict.SUPPORTED)

    def test_lab_different_value_contradicted(self):
        self.assertEqual(verdict_of("Labs: Hgb 9.0 g/dL."), Verdict.CONTRADICTED)

    def test_lab_within_tolerance_supported(self):
        self.assertEqual(verdict_of("Labs: Hgb 12.15 g/dL.", tolerance=0.1), Verdict.SUPPORTED)

    def test_lab_without_unit_is_not_convertible(self):
        assertion = decompose("Labs: Hgb 12.1.", hints={})[0]
        with self.assertRaises(PipelineError) as ctx:
            verify(assertion, BUNDLE, 0.0)
        self.assertEqual(ctx.exception.error_class, ErrorClass.UNIT_NOT_CONVERTIBLE)

    def test_lab_unit_mismatch_is_not_convertible(self):
        assertion = decompose("Labs: Hgb 12.1 mmol/L.")[0]
        with self.assertRaises(PipelineError) as ctx:
            verify(assertion, BUNDLE, 0.0)
        self.assertEqual(ctx.exception.error_class, ErrorClass.UNIT_NOT_CONVERTIBLE)

    def test_incomplete_resource_type_is_retrieval_partial(self):
        bundle = BUNDLE.model_copy(update={"incomplete_types": {"observations"}})
        assertion = decompose("Labs: Hgb 12.1 g/dL.")[0]
        with self.assertRaises(PipelineError) as ctx:
            verify(assertion, bundle, 0.0)
        self.assertEqual(ctx.exception.error_class, ErrorClass.RETRIEVAL_PARTIAL)

    def test_evidence_points_to_resource_ids_not_text(self):
        assertion = decompose("Patient on metformin.", hints=BUNDLE.hints())[0]
        evidence = verify(assertion, BUNDLE, 0.0).evidence
        self.assertEqual([(e.resource_type, e.resource_id) for e in evidence], [("MedicationRequest", "mr-1")])


class NliThresholdTests(unittest.TestCase):
    def test_entailment_supported(self):
        self.assertEqual(verdict_from_scores({"entailment": 0.9, "contradiction": 0.05}, 0.85, 0.85), Verdict.SUPPORTED)

    def test_contradiction_contradicted(self):
        self.assertEqual(verdict_from_scores({"entailment": 0.05, "contradiction": 0.9}, 0.85, 0.85), Verdict.CONTRADICTED)

    def test_contradiction_takes_precedence_over_entailment(self):
        self.assertEqual(verdict_from_scores({"entailment": 0.9, "contradiction": 0.9}, 0.85, 0.85), Verdict.CONTRADICTED)

    def test_ambiguous_scores_are_ungrounded(self):
        self.assertEqual(verdict_from_scores({"entailment": 0.5, "contradiction": 0.4}, 0.85, 0.85), Verdict.UNGROUNDED)

    def test_missing_model_is_nli_unavailable(self):
        with self.assertRaises(PipelineError) as ctx:
            nli_verdict(None, "premise", "hypothesis", POLICY)
        self.assertEqual(ctx.exception.error_class, ErrorClass.NLI_UNAVAILABLE)

    def test_failing_model_is_nli_unavailable(self):
        class Broken:
            def score(self, premise, hypothesis):
                raise RuntimeError("boom")

        with self.assertRaises(PipelineError) as ctx:
            nli_verdict(Broken(), "premise", "hypothesis", POLICY)
        self.assertEqual(ctx.exception.error_class, ErrorClass.NLI_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
