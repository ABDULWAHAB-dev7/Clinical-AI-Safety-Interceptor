"""Contract tests for the decision engine: known-good and known-bad cases (architecture 5, tests/contract)."""

import unittest

from core.errors import ErrorClass
from core.models import AssertionType, Outcome, Verdict, VerdictRecord
from core.policy import C1, R1, R2, R3, R4, Policy, decide

POLICY = Policy("contract", frozenset({"soap_note"}), 0.85, 0.85, 0.0)


def rec(verdict):
    return VerdictRecord(assertion_id="a1", type=AssertionType.OTHER_CLINICAL, verdict=verdict, verifier="none")


class HardStopContractTests(unittest.TestCase):
    def test_known_good_passes_through(self):
        d = decide(POLICY, [rec(Verdict.SUPPORTED)], [], 1)
        self.assertEqual(d.outcome, Outcome.PASS_THROUGH)
        self.assertEqual(d.rules_fired, [])

    def test_ungrounded_only_warns_and_is_not_blocked(self):
        d = decide(POLICY, [rec(Verdict.UNGROUNDED)], [], 1)
        self.assertEqual(d.outcome, Outcome.WARN)
        self.assertEqual(d.rules_fired, [C1])

    def test_contradiction_hard_blocks(self):
        d = decide(POLICY, [rec(Verdict.SUPPORTED), rec(Verdict.CONTRADICTED)], [], 2)
        self.assertEqual(d.outcome, Outcome.HARD_BLOCK)
        self.assertIn(R3, d.rules_fired)

    def test_verification_error_hard_blocks(self):
        d = decide(POLICY, [rec(Verdict.VERIFICATION_ERROR)], [], 1)
        self.assertEqual(d.outcome, Outcome.HARD_BLOCK)
        self.assertIn(R2, d.rules_fired)

    def test_pipeline_error_hard_blocks(self):
        d = decide(POLICY, [], [ErrorClass.RETRIEVAL_UNAVAILABLE.value], 0)
        self.assertEqual(d.outcome, Outcome.HARD_BLOCK)
        self.assertIn(R1, d.rules_fired)

    def test_no_assertions_hard_blocks(self):
        d = decide(POLICY, [], [], 0)
        self.assertEqual(d.outcome, Outcome.HARD_BLOCK)
        self.assertIn(R4, d.rules_fired)

    def test_every_rule_is_evaluated_for_reproducibility(self):
        d = decide(POLICY, [rec(Verdict.SUPPORTED)], [], 1)
        self.assertEqual(d.rules_evaluated, [R1, R2, R3, R4, C1])
        self.assertEqual(d.policy_version, "contract")


if __name__ == "__main__":
    unittest.main()
