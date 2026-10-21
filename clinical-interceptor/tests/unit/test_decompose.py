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

    def test_section_header_is_a_tag_not_claim_text(self):
        text = "CHIEF COMPLAINT: Cough for three days."
        a = decompose(text)[0]
        self.assertEqual(a.text, "Cough for three days.")
        self.assertEqual(a.section, "chief_complaint")
        self.assertEqual(text[a.span_start:a.span_end], a.text)

    def test_assessment_and_plan_header_sets_the_plan_tag(self):
        a = decompose("ASSESSMENT & PLAN: Continue metformin.")[0]
        self.assertEqual((a.section, a.type), ("plan", AssertionType.PLAN))
        self.assertEqual(a.text, "Continue metformin.")

    def test_bullets_and_numbers_are_removed_and_each_item_is_a_span(self):
        text = "- Started lisinopril 10 mg daily\n- Patient denies cough\n1. Labs reviewed.\n2) HbA1c 8.2% on 2026-09-02."
        spans = decompose(text)
        self.assertEqual([a.text for a in spans], ["Started lisinopril 10 mg daily", "Patient denies cough",
                                                   "Labs reviewed.", "HbA1c 8.2% on 2026-09-02."])
        for a in spans:
            self.assertEqual(text[a.span_start:a.span_end], a.text)

    def test_semicolon_splits_a_sentence(self):
        text = "Denies fever; started lisinopril 10 mg daily."
        spans = decompose(text)
        self.assertEqual([a.text for a in spans], ["Denies fever", "started lisinopril 10 mg daily."])
        self.assertEqual([a.type for a in spans], [AssertionType.NEGATED_FINDING, AssertionType.MEDICATION])

    def test_coordinate_clause_splits_into_two_atomic_spans(self):
        text = "Started lisinopril 10mg daily, and patient denies cough."
        spans = decompose(text)
        self.assertEqual([a.text for a in spans], ["Started lisinopril 10mg daily", "patient denies cough."])
        self.assertTrue(spans[1].negated)
        for a in spans:
            self.assertEqual(text[a.span_start:a.span_end], a.text)

    def test_noun_coordination_is_not_split(self):
        self.assertEqual(len(decompose("Patient has diabetes and hypertension.")), 1)

    def test_coordinate_split_needs_a_clause_after_it(self):
        self.assertEqual(len(decompose("Denies pain, and fever.")), 1)

    def test_soap_headers_are_tags_not_claim_text(self):
        text = "SUBJECTIVE: Cough for three days.\nOBJECTIVE:\n- BP 142/88"
        spans = decompose(text)
        self.assertEqual([a.text for a in spans], ["Cough for three days.", "BP 142/88"])
        self.assertEqual([a.section for a in spans], ["subjective", "objective"])
        for a in spans:
            self.assertEqual(text[a.span_start:a.span_end], a.text)

    def test_label_after_bullet_tags_only_that_item(self):
        text = "- Labs: HbA1c 8.2 %\n- Denies cough."
        lab, negated = decompose(text)
        self.assertEqual((lab.type, lab.name, lab.value, lab.unit), (AssertionType.OBSERVATION_LAB, "HbA1c", 8.2, "%"))
        self.assertEqual((negated.section, negated.type), (None, AssertionType.NEGATED_FINDING))

    def test_current_medications_header_is_a_medication_section(self):
        a = decompose("CURRENT MEDICATIONS: lisinopril 10 mg daily.")[0]
        self.assertEqual((a.section, a.type), ("medications", AssertionType.MEDICATION))
        self.assertEqual(a.text, "lisinopril 10 mg daily.")

    def test_offsets_round_trip_on_a_messy_note(self):
        text = (
            "CHIEF COMPLAINT: Follow-up; HPI: 62 y/o with T2DM, seen today.\n"
            "ALLERGIES: penicillin (rash).\n"
            "- Metformin 500 mg BID, and lisinopril 10 mg daily.\n"
            "2. Patient denies chest pain; no fever.\n\n"
            "ASSESSMENT & PLAN: Labs: Hgb 12.1 g/dL."
        )
        spans = decompose(text)
        self.assertGreater(len(spans), 5)
        self.assertEqual(len({a.assertion_id for a in spans}), len(spans))
        for a in spans:
            self.assertEqual(text[a.span_start:a.span_end], a.text)
            self.assertTrue(a.text.strip())


if __name__ == "__main__":
    unittest.main()
