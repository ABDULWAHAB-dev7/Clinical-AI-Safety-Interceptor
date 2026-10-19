"""Run with: python -m unittest discover -s tests -v"""

import json
import pathlib
import unittest

from interceptor import InvalidRequest, intercept, load_reference

ROOT = pathlib.Path(__file__).resolve().parent.parent
FORMULARY, INTERACTIONS = load_reference()


def run(patient=None, text="", orders=None):
    request = {
        "patient": patient if patient is not None else {"id": "SYN-T", "allergies": [], "medications": []},
        "output": {"text": text, "orders": orders or []},
    }
    return intercept(request, FORMULARY, INTERACTIONS)


def rules_hit(result):
    return {f["rule"] for f in result["findings"]}


class DecisionTests(unittest.TestCase):
    def test_clean_output_is_allowed(self):
        result = run(text="Take as directed.", orders=[{"drug": "paracetamol", "dose_mg": 500, "frequency_per_day": 4}])
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["findings"], [])

    def test_missing_patient_id_blocks(self):
        result = run(patient={"id": "  "}, text="Take as directed.")
        self.assertEqual(result["decision"], "block")
        self.assertIn("missing_patient_id", rules_hit(result))

    def test_allergy_conflict_blocks(self):
        result = run(patient={"id": "SYN-T", "allergies": ["Ibuprofen"]},
                     orders=[{"drug": "ibuprofen", "dose_mg": 200, "frequency_per_day": 2}])
        self.assertEqual(result["decision"], "block")
        self.assertIn("allergy_conflict", rules_hit(result))

    def test_single_dose_over_limit_blocks(self):
        result = run(orders=[{"drug": "paracetamol", "dose_mg": 1500, "frequency_per_day": 1}])
        self.assertEqual(result["decision"], "block")
        self.assertIn("dose_over_single_max", rules_hit(result))

    def test_daily_total_over_limit_blocks(self):
        result = run(orders=[{"drug": "ibuprofen", "dose_mg": 400, "frequency_per_day": 4}])
        self.assertEqual(result["decision"], "block")
        self.assertIn("dose_over_daily_max", rules_hit(result))

    def test_daily_total_at_limit_is_not_flagged(self):
        result = run(orders=[{"drug": "ibuprofen", "dose_mg": 400, "frequency_per_day": 3}])
        self.assertEqual(result["decision"], "allow")

    def test_unknown_drug_needs_review(self):
        result = run(orders=[{"drug": "made-up-drug", "dose_mg": 5, "frequency_per_day": 1}])
        self.assertEqual(result["decision"], "review")
        self.assertIn("unknown_drug", rules_hit(result))

    def test_interaction_needs_review_in_either_order(self):
        for drugs in (["warfarin", "ibuprofen"], ["ibuprofen", "warfarin"]):
            orders = [{"drug": d, "dose_mg": 1, "frequency_per_day": 1} for d in drugs]
            result = run(orders=orders)
            self.assertIn("interaction", rules_hit(result), drugs)
            self.assertNotEqual(result["decision"], "block", drugs)

    def test_interaction_with_current_medication_needs_review(self):
        result = run(patient={"id": "SYN-T", "allergies": [], "medications": ["warfarin"]},
                     orders=[{"drug": "ibuprofen", "dose_mg": 200, "frequency_per_day": 2}])
        self.assertEqual(result["decision"], "review")
        self.assertIn("interaction", rules_hit(result))

    def test_overclaim_needs_review(self):
        result = run(text="This dose is 100% safe for everyone.")
        self.assertEqual(result["decision"], "review")
        self.assertIn("overclaim", rules_hit(result))

    def test_block_outranks_review(self):
        result = run(text="Definitely safe.",
                     orders=[{"drug": "warfarin", "dose_mg": 50, "frequency_per_day": 1}])
        self.assertEqual(result["decision"], "block")


class ValidationTests(unittest.TestCase):
    def test_rejects_non_object_request(self):
        with self.assertRaises(InvalidRequest):
            intercept([], FORMULARY, INTERACTIONS)

    def test_rejects_boolean_dose(self):
        with self.assertRaises(InvalidRequest):
            run(orders=[{"drug": "paracetamol", "dose_mg": True, "frequency_per_day": 1}])

    def test_rejects_non_positive_frequency(self):
        with self.assertRaises(InvalidRequest):
            run(orders=[{"drug": "paracetamol", "dose_mg": 100, "frequency_per_day": 0}])


class ExampleFileTests(unittest.TestCase):
    def test_example_files(self):
        expected = {"clean.json": "allow", "unsafe.json": "block"}
        for name, decision in expected.items():
            with self.subTest(name=name):
                request = json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))
                self.assertEqual(intercept(request, FORMULARY, INTERACTIONS)["decision"], decision)


if __name__ == "__main__":
    unittest.main()
