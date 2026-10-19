import copy
import json
import pathlib
import tempfile
import unittest

from tests.support import ROOT
from tools.seed_check import run_checks

GOLD = ROOT / "seed" / "gold" / "case-0001.gold.json"


def tampered_gold(mutate):
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    mutate(gold)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(gold, tmp)
    tmp.close()
    return pathlib.Path(tmp.name)


class SeedAcceptanceTests(unittest.TestCase):
    def test_all_seed_checks_pass(self):
        failures, propositions, resources = run_checks()
        self.assertEqual(failures, [])
        self.assertEqual(propositions, 5)
        self.assertEqual(resources, 5)

    def test_S4_catches_the_hand_computed_offsets_from_the_design_doc(self):
        def use_doc_offsets(gold):
            # DATABASE.md 4.4 gave "drug allergies" as 66-80; the note has it at 69-83.
            entity = gold["propositions"][0]["entities"][0]
            entity["char_start"], entity["char_end"] = 66, 80

        path = tampered_gold(use_doc_offsets)
        try:
            failures, _, _ = run_checks(gold_path=path)
        finally:
            path.unlink()
        self.assertTrue(any(f.startswith("S4 clm_000000000001") for f in failures), failures)

    def test_S3_catches_dangling_reference(self):
        def dangle(gold):
            gold["propositions"][1]["expected_ref"] = "Condition/does-not-exist"

        path = tampered_gold(dangle)
        try:
            failures, _, _ = run_checks(gold_path=path)
        finally:
            path.unlink()
        self.assertTrue(any(f.startswith("S3 clm_000000000002") for f in failures), failures)

    def test_S5_catches_wrong_expected_risk(self):
        def wrong_risk(gold):
            gold["propositions"][4]["expected_risk"] = "SAFE"

        path = tampered_gold(wrong_risk)
        try:
            failures, _, _ = run_checks(gold_path=path)
        finally:
            path.unlink()
        self.assertTrue(any(f.startswith("S5 clm_000000000005") for f in failures), failures)

    def test_note_hash_mismatch_is_reported(self):
        def stale_hash(gold):
            gold["note_sha256"] = "0" * 64

        path = tampered_gold(stale_hash)
        try:
            failures, _, _ = run_checks(gold_path=path)
        finally:
            path.unlink()
        self.assertTrue(any("note_sha256" in f for f in failures))


if __name__ == "__main__":
    unittest.main()
