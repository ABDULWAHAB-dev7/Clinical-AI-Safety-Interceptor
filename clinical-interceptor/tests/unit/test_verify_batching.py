"""Tests for the batched NLI path in the verifier.

The verifier now scores every claim that needs the model in one call. These tests pin the two things that matter:
the result is the same as the per-claim path, and a failure still fails closed.

The claims used here reach the NLI branch: they have retrieval candidates but no coded match, so no rule settles
them. "Patient reports headaches" has no candidate at all, so it takes the deterministic no_candidates branch.
"""

import unittest

from interceptor.core.verify import VerificationError, verify_claim, verify_claims
from tests.support_verify import KNOWLEDGE, FakeNli, case_triples

TRIPLES = case_triples()
NEUTRAL = {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1}

# Claims that reach the NLI branch: each has one retrieval candidate and no coded match.
NLI_CLAIM = "Blood pressure is stable"
NLI_CLAIM_2 = "Diabetes follow-up today"


class BatchScorer(FakeNli):
    """A scorer that also answers batches, so the batched path is the one under test."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.batches = []

    def score_batch(self, pairs):
        self.batches.append(list(pairs))
        return [self.score(p, h) for p, h in pairs]


def claim(claim_id, text):
    return {"claim_id": claim_id, "claim_text": text}


class BatchingTests(unittest.TestCase):
    def test_claims_that_need_no_model_are_not_sent_to_it(self):
        scorer = BatchScorer()
        result, _ = verify_claim(claim("clm_000000000001", "Started amoxicillin 500 mg TID"), TRIPLES, KNOWLEDGE,
                                  "test", scorer)
        self.assertEqual(result["risk_rule"], "drug_allergy_conflict")
        self.assertEqual(scorer.batches, [])

    def test_a_claim_with_no_candidate_never_reaches_the_model(self):
        scorer = BatchScorer()
        result, _ = verify_claim(claim("clm_000000000001", "Patient reports headaches"), TRIPLES, KNOWLEDGE, "test",
                                 scorer)
        self.assertEqual(result["risk_rule"], "unsupported_clinical")
        self.assertEqual(scorer.batches, [])

    def test_every_claim_that_needs_the_model_goes_in_one_batch(self):
        scorer = BatchScorer()
        claims = [claim("clm_000000000001", NLI_CLAIM), claim("clm_000000000002", NLI_CLAIM_2)]
        verify_claims(claims, TRIPLES, KNOWLEDGE, "test", scorer)
        self.assertEqual(len(scorer.batches), 1)
        self.assertEqual(len(scorer.batches[0]), 2)

    def test_the_batch_result_is_the_same_as_the_per_claim_result(self):
        per_claim, _ = verify_claim(claim("clm_000000000001", NLI_CLAIM), TRIPLES, KNOWLEDGE, "test", FakeNli())
        batched, _ = verify_claim(claim("clm_000000000001", NLI_CLAIM), TRIPLES, KNOWLEDGE, "test", BatchScorer())
        self.assertEqual(per_claim["nli_class"], batched["nli_class"])
        self.assertEqual(per_claim["risk_severity"], batched["risk_severity"])
        self.assertEqual(per_claim["risk_rule"], batched["risk_rule"])
        self.assertEqual(per_claim["matched_fhir_ref"], batched["matched_fhir_ref"])

    def test_a_scorer_without_a_batch_method_still_works(self):
        # FakeNli has no score_batch, so the verifier falls back to one call per pair
        result, _ = verify_claim(claim("clm_000000000001", NLI_CLAIM), TRIPLES, KNOWLEDGE, "test",
                                  FakeNli(default=NEUTRAL))
        self.assertEqual(result["nli_class"], "NEUTRAL")
        self.assertEqual(result["risk_severity"], "WARNING")

    def test_the_trace_still_names_the_branch(self):
        _, trace = verify_claim(claim("clm_000000000001", NLI_CLAIM), TRIPLES, KNOWLEDGE, "test",
                                FakeNli(default=NEUTRAL))
        self.assertEqual(trace["branch"], "nli_neutral")


class FailClosedTests(unittest.TestCase):
    def test_no_model_at_all_fails_closed(self):
        with self.assertRaises(VerificationError):
            verify_claim(claim("clm_000000000001", NLI_CLAIM), TRIPLES, KNOWLEDGE, "test", None)

    def test_a_model_that_raises_fails_closed(self):
        with self.assertRaises(VerificationError):
            verify_claim(claim("clm_000000000001", NLI_CLAIM), TRIPLES, KNOWLEDGE, "test",
                         FakeNli(error=RuntimeError("model died")))

    def test_a_model_that_returns_a_broken_batch_fails_closed(self):
        class BrokenBatch:
            def score_batch(self, pairs):
                return [{"entailment": 2.0, "neutral": 0.0, "contradiction": 0.0}]

            def score(self, premise, hypothesis):
                raise AssertionError("the per-claim path must not be used")

        with self.assertRaises(VerificationError):
            verify_claim(claim("clm_000000000001", NLI_CLAIM), TRIPLES, KNOWLEDGE, "test", BrokenBatch())

    def test_a_model_that_returns_the_wrong_number_of_scores_fails_closed(self):
        class ShortBatch:
            def score_batch(self, pairs):
                return []

            def score(self, premise, hypothesis):
                raise AssertionError("the per-claim path must not be used")

        with self.assertRaises(VerificationError):
            verify_claim(claim("clm_000000000001", NLI_CLAIM), TRIPLES, KNOWLEDGE, "test", ShortBatch())

    def test_one_failing_claim_fails_the_whole_batch(self):
        with self.assertRaises(VerificationError):
            verify_claims([claim("clm_000000000001", NLI_CLAIM), claim("clm_000000000002", NLI_CLAIM_2)],
                          TRIPLES, KNOWLEDGE, "test", FakeNli(error=RuntimeError("model died")))


if __name__ == "__main__":
    unittest.main()
