import unittest

from interceptor.core.decompose import decompose
from interceptor.core.errors import ErrorClass, PipelineError
from interceptor.core.models import AssertionType
from tests.support import load_example


class DecomposeTests(unittest.TestCase):
    def test_span_offsets_round_trip_on_example(self):
        text = load_example("verify_clean.json")["document_text"]
        assertions = decompose(text)
        self.assertGreater(len(assertions), 0)
        for a in assertions:
            self.assertEqual(text[a.span_start:a.span_end], a.text)

    def test_section_headers_set_types(self):
        text = "Allergies: penicillin.\nMedications: metformin 500 mg twice daily.\nLabs: Hgb 12.1 g/dL."
        types = [a.type for a in decompose(text)]
        self.assertEqual(types, [AssertionType.ALLERGY, AssertionType.MEDICATION, AssertionType.OBSERVATION_LAB])

    def test_decimal_point_does_not_split_sentence(self):
        assertions = decompose("Labs: Hgb 12.1 g/dL.")
        self.assertEqual(len(assertions), 1)
        self.assertEqual((assertions[0].name, assertions[0].value, assertions[0].unit), ("Hgb", 12.1, "g/dL"))

    def test_lab_without_unit_keeps_unit_none(self):
        lab = decompose("Labs: Hgb 12.1.")[0]
        self.assertIsNone(lab.unit)

    def test_hints_guide_typing(self):
        assertion = decompose("Patient on metformin.", hints={"metformin": AssertionType.MEDICATION})[0]
        self.assertEqual(assertion.type, AssertionType.MEDICATION)

    def test_negation_detected(self):
        assertion = decompose("No pleural effusion.")[0]
        self.assertTrue(assertion.negated)
        self.assertEqual(assertion.type, AssertionType.NEGATED_FINDING)

    def test_non_string_is_schema_invalid(self):
        with self.assertRaises(PipelineError) as ctx:
            decompose(None)
        self.assertEqual(ctx.exception.error_class, ErrorClass.DECOMPOSITION_SCHEMA_INVALID)


if __name__ == "__main__":
    unittest.main()
