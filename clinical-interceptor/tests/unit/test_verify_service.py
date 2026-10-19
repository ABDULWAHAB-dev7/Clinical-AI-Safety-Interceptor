import unittest

from interceptor.core.verify import VerificationError, verify_claim
from tests.support_verify import KNOWLEDGE, FakeNli, case_triples, with_thresholds
from tools.gold_check_verify import run_gold_check

TRIPLES = case_triples()
NO_ALLERGY = [t for t in TRIPLES if t.predicate != "HAS_ALLERGY"]
NEUTRAL = {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1}


def verify(text, triples=TRIPLES, nli=None, knowledge=KNOWLEDGE):
    return verify_claim({"claim_id": "clm_000000000001", "claim_text": text}, triples, knowledge, "test", nli)


def summary(result):
    return (result["nli_class"], result["risk_severity"], result["risk_rule"], result["matched_fhir_ref"])


class BranchTests(unittest.TestCase):
    def test_allergy_denial_against_a_documented_allergy(self):
        result, trace = verify("Patient denies known drug allergies")
        self.assertEqual(summary(result), ("CONTRADICTION", "CRITICAL", "contradiction_high_risk",
                                           "AllergyIntolerance/allergy-penicillin-001"))
        self.assertEqual(trace["branch"], "allergy_denied_but_documented")

    def test_allergy_denial_with_no_allergy_record_is_unsupported_not_safe(self):
        result, trace = verify("No known drug allergies", NO_ALLERGY)
        self.assertEqual(summary(result), ("NEUTRAL", "WARNING", "unsupported_clinical", None))
        self.assertEqual(trace["branch"], "no_allergy_record")

    def test_documented_allergy_is_entailed(self):
        result, _ = verify("Patient has drug allergies")
        self.assertEqual(summary(result)[:3], ("ENTAILMENT", "SAFE", "entailed"))

    def test_condition_documented_and_denied(self):
        self.assertEqual(summary(verify("Patient has type 2 diabetes")[0])[:3], ("ENTAILMENT", "SAFE", "entailed"))
        self.assertEqual(summary(verify("No history of type 2 diabetes")[0])[:3],
                         ("CONTRADICTION", "CRITICAL", "contradiction_high_risk"))

    def test_denying_an_inactive_condition_is_only_a_warning(self):
        inactive = [t if t.predicate != "HAS_CONDITION" else _replace(t, status="resolved") for t in TRIPLES]
        self.assertEqual(summary(verify("No history of type 2 diabetes", inactive)[0])[:3],
                         ("CONTRADICTION", "WARNING", "contradiction"))

    def test_lab_value_within_tolerance_is_entailed_and_outside_is_a_warning(self):
        self.assertEqual(summary(verify("HbA1c was 8.2% on 2026-09-02")[0])[:3], ("ENTAILMENT", "SAFE", "entailed"))
        self.assertEqual(summary(verify("HbA1c was 6.1% on 2026-09-02")[0])[:3],
                         ("CONTRADICTION", "WARNING", "contradiction"))
        loose = with_thresholds(value_tolerance_abs=0.5)
        self.assertEqual(summary(verify("HbA1c was 8.6%", knowledge=loose)[0])[:3], ("ENTAILMENT", "SAFE", "entailed"))

    def test_the_date_in_a_lab_claim_is_not_read_as_the_value(self):
        # the first number after HbA1c is 8.2; 2026 from the date must be ignored
        self.assertEqual(verify("HbA1c on 2026-09-02 was 8.2%")[0]["nli_class"], "ENTAILMENT")

    def test_claim_with_no_candidate_is_unsupported(self):
        result, trace = verify("Patient reports headaches")
        self.assertEqual(summary(result), ("NEUTRAL", "WARNING", "unsupported_clinical", None))
        self.assertEqual(trace["branch"], "no_candidates")


class RuleTests(unittest.TestCase):
    def test_rule_1_drug_in_an_allergy_class_is_critical_while_nli_stays_neutral(self):
        result, trace = verify("Started amoxicillin 500 mg TID")
        self.assertEqual(summary(result), ("NEUTRAL", "CRITICAL", "drug_allergy_conflict",
                                           "AllergyIntolerance/allergy-penicillin-001"))
        self.assertFalse(trace["drug_class_unknown"])

    def test_rule_2_dose_above_the_limit_without_an_allergy(self):
        result, trace = verify("Started amoxicillin 1500 mg once", NO_ALLERGY)
        self.assertEqual(summary(result), ("CONTRADICTION", "CRITICAL", "contradiction_high_risk", None))
        self.assertEqual(trace["branch"], "dose_limit_exceeded")

    def test_dose_within_the_limit_without_an_allergy_is_only_unsupported(self):
        result, _ = verify("Started amoxicillin 500 mg TID", NO_ALLERGY)
        self.assertEqual(summary(result)[:3], ("NEUTRAL", "WARNING", "unsupported_clinical"))

    def test_rule_order_rule_1_beats_rule_2_and_the_nli_class_is_kept(self):
        result, _ = verify("Started amoxicillin 1500 mg once")
        self.assertEqual(result["risk_rule"], "drug_allergy_conflict")
        self.assertEqual(result["nli_class"], "CONTRADICTION")  # a rule never changes the NLI class

    def test_unknown_drug_class_is_traced_and_rule_1_does_not_fire(self):
        result, trace = verify("Started lisinopril 10 mg daily")
        self.assertTrue(trace["drug_class_unknown"])
        self.assertEqual(result["risk_rule"], "unsupported_clinical")

    def test_a_drug_with_no_lexicon_match_is_not_checked_by_rule_1(self):
        result, _ = verify("Started warfarin 5 mg daily")
        self.assertNotEqual(result["risk_rule"], "drug_allergy_conflict")

    def test_trace_holds_refs_and_scores_but_no_claim_text(self):
        _, trace = verify("Started amoxicillin 500 mg TID")
        self.assertNotIn("amoxicillin", str(trace).lower())
        self.assertEqual(trace["candidates"][0]["source_ref"], "AllergyIntolerance/allergy-penicillin-001")


class NliTests(unittest.TestCase):
    CLAIM = "Blood pressure is stable"  # shares "blood" with the HbA1c fact, so the NLI scorer is needed

    def test_without_a_model_an_unresolved_claim_fails_closed(self):
        with self.assertRaises(VerificationError):
            verify(self.CLAIM)

    def test_neutral_scores_give_an_unsupported_warning(self):
        result, trace = verify(self.CLAIM, nli=FakeNli(default=NEUTRAL))
        self.assertEqual(summary(result), ("NEUTRAL", "WARNING", "unsupported_clinical", None))
        self.assertEqual(trace["branch"], "nli_neutral")

    def test_strong_entailment_is_safe_with_a_reference(self):
        nli = FakeNli(default={"entailment": 0.95, "neutral": 0.03, "contradiction": 0.02})
        result, _ = verify(self.CLAIM, nli=nli)
        self.assertEqual(summary(result), ("ENTAILMENT", "SAFE", "entailed", "Observation/obs-hba1c-001"))

    def test_weak_entailment_is_a_low_confidence_warning(self):
        nli = FakeNli(default={"entailment": 0.6, "neutral": 0.3, "contradiction": 0.1})
        self.assertEqual(verify(self.CLAIM, nli=nli)[0]["risk_rule"], "low_confidence_entailment")

    def test_entailment_floor_comes_from_the_thresholds_file(self):
        nli = FakeNli(default={"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05})
        strict = with_thresholds(entailment_min=0.95)
        self.assertEqual(verify(self.CLAIM, nli=nli, knowledge=strict)[0]["risk_rule"], "low_confidence_entailment")

    def test_contradiction_over_the_threshold_wins_even_with_a_stronger_entailment_elsewhere(self):
        claim = "Blood diabetes"  # candidates: the condition (diabetes) and the HbA1c observation (blood)
        nli = FakeNli(by_source={
            "diabetes": {"entailment": 0.97, "neutral": 0.02, "contradiction": 0.01},
            "Hemoglobin": {"entailment": 0.05, "neutral": 0.1, "contradiction": 0.85},
        })
        result, trace = verify(claim, nli=nli)
        self.assertEqual(result["nli_class"], "CONTRADICTION")
        self.assertEqual(result["matched_fhir_ref"], "Observation/obs-hba1c-001")
        self.assertEqual(trace["branch"], "nli_contradiction")

    def test_contradiction_below_the_threshold_is_not_enough(self):
        nli = FakeNli(default={"entailment": 0.2, "neutral": 0.4, "contradiction": 0.4})
        self.assertEqual(verify(self.CLAIM, nli=nli)[0]["nli_class"], "NEUTRAL")

    def test_a_slow_model_times_out_and_fails_closed(self):
        slow = with_thresholds(nli_timeout_seconds=0.05)
        with self.assertRaises(VerificationError) as ctx:
            verify(self.CLAIM, nli=FakeNli(default=NEUTRAL, delay=0.5), knowledge=slow)
        self.assertIn("timed out", ctx.exception.reason)

    def test_a_failing_model_fails_closed(self):
        with self.assertRaises(VerificationError):
            verify(self.CLAIM, nli=FakeNli(error=RuntimeError("boom")))

    def test_malformed_scores_fail_closed(self):
        with self.assertRaises(VerificationError):
            verify(self.CLAIM, nli=FakeNli(default={"entailment": 0.9}))

    def test_out_of_range_scores_fail_closed(self):
        with self.assertRaises(VerificationError):
            verify(self.CLAIM, nli=FakeNli(default={"entailment": 1.5, "neutral": 0.0, "contradiction": 0.0}))


class GoldCheckTests(unittest.TestCase):
    def test_without_a_model_four_of_five_match_and_claim_5_needs_nli(self):
        report = run_gold_check()
        self.assertEqual(report["matched"], {"nli_class": 4, "matched_fhir_ref": 4, "risk_severity": 4,
                                             "risk_rule": 4})
        self.assertEqual([c for c, _ in report["needs_nli"]], ["clm_000000000005"])
        self.assertEqual(report["mismatches"], [])

    def test_with_a_neutral_model_all_five_match_gold(self):
        report = run_gold_check(nli_model=FakeNli(default=NEUTRAL))
        self.assertEqual(report["matched"], {"nli_class": 5, "matched_fhir_ref": 5, "risk_severity": 5,
                                             "risk_rule": 5})
        self.assertEqual((report["needs_nli"], report["mismatches"]), ([], []))


def _replace(triple, **changes):
    from dataclasses import replace
    return replace(triple, **changes)


if __name__ == "__main__":
    unittest.main()
