"""The decomposer gold check runs and reports; it is not expected to pass (see tools/gold_check_decompose.py)."""

import unittest

from tools.gold_check_decompose import run_decompose_check


class DecomposeGoldCheckTests(unittest.TestCase):
    def test_every_gold_claim_is_accounted_for_and_the_numbers_do_not_get_worse(self):
        result = run_decompose_check()
        self.assertEqual(result["gold"], 5)
        self.assertEqual(result["missing"], [])  # no gold claim is lost entirely
        self.assertGreaterEqual(len(result["exact"]) + len(result["same_span"]), 3)  # today: 3 of 5 share a span


if __name__ == "__main__":
    unittest.main()
