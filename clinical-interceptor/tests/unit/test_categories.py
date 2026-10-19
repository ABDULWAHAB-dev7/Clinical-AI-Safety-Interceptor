import unittest
import uuid

from interceptor.core.categories import classification_for, explanation, hallucination_category
from interceptor.core.contracts import CodeSystem, NLIClass, RiskSeverity, VerificationResult
from interceptor.core.entities import Entity
from tests.support_verify import KNOWLEDGE

ALLERGY = Entity(CodeSystem.SNOMED_CT, "416098002", "Drug allergy", "drug allergies", 0, 14)
AMOXICILLIN = Entity(CodeSystem.RXNORM, "723", "Amoxicillin", "amoxicillin", 0, 11)
LISINOPRIL = Entity(CodeSystem.RXNORM, "29046", "Lisinopril", "lisinopril", 0, 10)
T2DM = Entity(CodeSystem.SNOMED_CT, "44054006", "Diabetes mellitus type 2", "type 2 diabetes", 0, 15)
HBA1C = Entity(CodeSystem.LOINC, "4548-4", "HbA1c", "HbA1c", 0, 5)


def result(nli, rule=None, ref=None, severity=RiskSeverity.WARNING):
    return VerificationResult(result_id=uuid.uuid4(), claim_id="clm_000000000001", matched_fhir_ref=ref,
                              nli_class=nli, confidence=0.9, risk_severity=severity, risk_rule=rule,
                              verifier_version="t")


def category(res, text="", entities=()):
    return hallucination_category(res, text, list(entities), KNOWLEDGE)


class CategoryTableTests(unittest.TestCase):
    """One test per row of API.md 7.1.2, plus the order of the rows."""

    def test_row_1_rule_drug_allergy_conflict(self):
        r = result(NLIClass.NEUTRAL, "drug_allergy_conflict", severity=RiskSeverity.CRITICAL)
        self.assertEqual(category(r, "Started amoxicillin", [AMOXICILLIN]), "DRUG_ALLERGY_CONFLICT")

    def test_row_1_contradiction_with_an_allergy_entity(self):
        r = result(NLIClass.CONTRADICTION, "contradiction_high_risk", severity=RiskSeverity.CRITICAL)
        self.assertEqual(category(r, "No known drug allergies", [ALLERGY]), "DRUG_ALLERGY_CONFLICT")

    def test_row_1_contradiction_with_a_drug_in_an_allergy_class(self):
        r = result(NLIClass.CONTRADICTION, "contradiction_high_risk", severity=RiskSeverity.CRITICAL)
        self.assertEqual(category(r, "amoxicillin 5000 mg", [AMOXICILLIN]), "DRUG_ALLERGY_CONFLICT")

    def test_row_2_medication_with_a_dose_or_frequency(self):
        r = result(NLIClass.CONTRADICTION, "contradiction_high_risk", severity=RiskSeverity.CRITICAL)
        self.assertEqual(category(r, "lisinopril 400 mg daily", [LISINOPRIL]), "MEDICATION_DOSE_MISMATCH")

    def test_row_2_needs_a_dose_or_frequency(self):
        r = result(NLIClass.CONTRADICTION, "contradiction", ref=None)
        self.assertEqual(category(r, "Started lisinopril", [LISINOPRIL]), "OTHER")

    def test_row_3_diagnosis_contradiction(self):
        r = result(NLIClass.CONTRADICTION, "contradiction_high_risk", severity=RiskSeverity.CRITICAL)
        self.assertEqual(category(r, "No history of type 2 diabetes", [T2DM]), "DIAGNOSIS_CONTRADICTION")

    def test_row_4_lab_value_mismatch_on_contradiction(self):
        r = result(NLIClass.CONTRADICTION, "contradiction", ref="Observation/obs-1")
        self.assertEqual(category(r, "HbA1c was 9.5%", [HBA1C]), "LAB_VALUE_MISMATCH")

    def test_row_4_lab_value_mismatch_on_neutral(self):
        r = result(NLIClass.NEUTRAL, "unsupported_clinical", ref="Observation/obs-1")
        self.assertEqual(category(r, "HbA1c was recorded", [HBA1C]), "LAB_VALUE_MISMATCH")

    def test_row_5_temporal_mismatch_is_not_evaluated(self):
        # the audit request carries no date for the matched fact, so a date alone never gives TEMPORAL_MISMATCH
        r = result(NLIClass.CONTRADICTION, "contradiction")
        self.assertNotEqual(category(r, "HbA1c was 8.2% on 2020-01-01", []), "TEMPORAL_MISMATCH")

    def test_row_6_unsupported_claim(self):
        r = result(NLIClass.NEUTRAL, "unsupported_clinical")
        self.assertEqual(category(r, "Blood pressure is stable"), "UNSUPPORTED_CLAIM")

    def test_row_7_anything_else_is_other(self):
        self.assertEqual(category(result(NLIClass.CONTRADICTION, "contradiction"), "something"), "OTHER")

    def test_order_allergy_row_wins_over_the_dose_row(self):
        r = result(NLIClass.CONTRADICTION, "contradiction_high_risk", severity=RiskSeverity.CRITICAL)
        self.assertEqual(category(r, "amoxicillin 5000 mg once", [AMOXICILLIN]), "DRUG_ALLERGY_CONFLICT")

    def test_order_diagnosis_row_wins_over_the_lab_row(self):
        r = result(NLIClass.CONTRADICTION, "contradiction_high_risk", ref="Observation/obs-1")
        self.assertEqual(category(r, "No type 2 diabetes", [T2DM]), "DIAGNOSIS_CONTRADICTION")


class AttributionHelperTests(unittest.TestCase):
    def test_classification_follows_the_nli_class(self):
        self.assertEqual(classification_for(NLIClass.ENTAILMENT), "SOURCE_SUPPORTED")
        self.assertEqual(classification_for(NLIClass.CONTRADICTION), "SOURCE_CONTRADICTED")
        self.assertEqual(classification_for(NLIClass.NEUTRAL), "NO_SOURCE_EVIDENCE")

    def test_explanations_are_generated_text_with_refs_only(self):
        res = {"matched_fhir_ref": "AllergyIntolerance/a-1", "risk_rule": "drug_allergy_conflict"}
        self.assertIn("AllergyIntolerance/a-1", explanation("SOURCE_CONTRADICTED", res))
        self.assertIn("allergy", explanation("NO_SOURCE_EVIDENCE", res).lower())
        self.assertTrue(explanation("NON_CLINICAL", None))
        self.assertIn("supported", explanation("SOURCE_SUPPORTED", None))


if __name__ == "__main__":
    unittest.main()
