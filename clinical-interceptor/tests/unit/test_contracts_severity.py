import unittest
import uuid

from pydantic import ValidationError

from interceptor.core.contracts import (
    AtomicProposition,
    AttributionClass,
    AttributionSpan,
    CodeSystem,
    MedicalEntity,
    NLIClass,
    RiskSeverity,
    VerificationResult,
)
from interceptor.core.pseudo import EID_PATTERN, PID_PATTERN, encounter_pseudo_id, patient_pseudo_id
from interceptor.core.severity import ClaimFacts, classify_risk
from interceptor.core.terminology import entity_kind, matches_allergy

KEY = b"unit-test-key-0123456789"
NOTE = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


def facts(**overrides):
    base = dict(
        nli_class=NLIClass.NEUTRAL,
        confidence=0.5,
        is_clinical=True,
        is_medication_claim=False,
        matches_allergy=False,
        entity_kinds=frozenset(),
    )
    base.update(overrides)
    return ClaimFacts(**base)


class SeverityTests(unittest.TestCase):
    def test_priority_one_drug_allergy_conflict(self):
        self.assertEqual(
            classify_risk(facts(is_medication_claim=True, matches_allergy=True)),
            (RiskSeverity.CRITICAL, "drug_allergy_conflict"),
        )

    def test_priority_two_contradiction_on_high_risk_entity(self):
        self.assertEqual(
            classify_risk(facts(nli_class=NLIClass.CONTRADICTION, entity_kinds=frozenset({"allergy"}))),
            (RiskSeverity.CRITICAL, "contradiction_high_risk"),
        )

    def test_priority_three_contradiction_other(self):
        self.assertEqual(
            classify_risk(facts(nli_class=NLIClass.CONTRADICTION)),
            (RiskSeverity.WARNING, "contradiction"),
        )

    def test_priority_four_unsupported_clinical(self):
        self.assertEqual(classify_risk(facts()), (RiskSeverity.WARNING, "unsupported_clinical"))

    def test_non_clinical_neutral_is_safe_without_rule(self):
        self.assertEqual(classify_risk(facts(is_clinical=False)), (RiskSeverity.SAFE, None))

    def test_priority_five_entailed(self):
        self.assertEqual(
            classify_risk(facts(nli_class=NLIClass.ENTAILMENT, confidence=0.85)),
            (RiskSeverity.SAFE, "entailed"),
        )

    def test_priority_six_low_confidence_entailment(self):
        self.assertEqual(
            classify_risk(facts(nli_class=NLIClass.ENTAILMENT, confidence=0.84)),
            (RiskSeverity.WARNING, "low_confidence_entailment"),
        )

    def test_rule_order_medication_allergy_beats_contradiction(self):
        self.assertEqual(
            classify_risk(facts(nli_class=NLIClass.CONTRADICTION, is_medication_claim=True, matches_allergy=True)),
            (RiskSeverity.CRITICAL, "drug_allergy_conflict"),
        )

    def test_matches_allergy_uses_class_not_exact_code(self):
        self.assertTrue(matches_allergy(["723"], ["91936005"]))  # amoxicillin vs penicillin allergy
        self.assertFalse(matches_allergy(["999999"], ["91936005"]))

    def test_entity_kind(self):
        self.assertEqual(entity_kind(CodeSystem.SNOMED_CT, "416098002", set()), "allergy")
        self.assertEqual(entity_kind(CodeSystem.RXNORM, "723", set()), "medication_dose")
        self.assertEqual(entity_kind(CodeSystem.SNOMED_CT, "44054006", {"44054006"}), "active_diagnosis")
        self.assertIsNone(entity_kind(CodeSystem.LOINC, "4548-4", set()))


class ContractTests(unittest.TestCase):
    def entity(self, **overrides):
        base = dict(system="http://snomed.info/sct", code="44054006", display="Diabetes", surface_text="x",
                    char_start=0, char_end=1)
        base.update(overrides)
        return MedicalEntity(**base)

    def test_entity_requires_positive_span(self):
        with self.assertRaises(ValidationError):
            self.entity(char_start=5, char_end=5)

    def test_proposition_claim_must_lie_inside_sentence(self):
        with self.assertRaises(ValidationError):
            AtomicProposition(
                claim_id="clm_000000000001", note_id=NOTE, source_sentence="Abc.", source_sentence_start=10,
                source_sentence_end=14, claim_text="Abc", char_start=5, char_end=8,
            )

    def test_proposition_claim_id_pattern(self):
        with self.assertRaises(ValidationError):
            AtomicProposition(
                claim_id="clm_1", note_id=NOTE, source_sentence="Abc.", source_sentence_start=0,
                source_sentence_end=4, claim_text="Abc", char_start=0, char_end=3,
            )

    def test_entailment_requires_matched_ref(self):
        with self.assertRaises(ValidationError):
            VerificationResult(
                result_id=uuid.uuid4(), claim_id="clm_000000000002", nli_class=NLIClass.ENTAILMENT,
                confidence=0.9, risk_severity=RiskSeverity.SAFE, verifier_version="v1",
            )

    def test_entailment_critical_needs_rule_override(self):
        with self.assertRaises(ValidationError):
            VerificationResult(
                result_id=uuid.uuid4(), claim_id="clm_000000000002", nli_class=NLIClass.ENTAILMENT,
                matched_fhir_ref="Condition/cond-1", confidence=0.9, risk_severity=RiskSeverity.CRITICAL,
                verifier_version="v1",
            )

    def test_neutral_result_without_ref_is_valid(self):
        result = VerificationResult(
            result_id=uuid.uuid4(), claim_id="clm_000000000005", nli_class=NLIClass.NEUTRAL, confidence=0.58,
            risk_severity=RiskSeverity.WARNING, risk_rule="unsupported_clinical", verifier_version="v1",
        )
        self.assertIsNone(result.matched_fhir_ref)

    def test_attribution_range_must_be_non_empty(self):
        with self.assertRaises(ValidationError):
            AttributionSpan(
                span_id=uuid.uuid4(), note_id=NOTE, doc_char_start=4, doc_char_end=4,
                classification=AttributionClass.NO_SOURCE_EVIDENCE, explanation="x",
            )


class PseudoIdTests(unittest.TestCase):
    def test_patient_id_format(self):
        self.assertRegex(patient_pseudo_id("SYNTH-MRN-0001", KEY), PID_PATTERN)

    def test_normalization_makes_ids_stable(self):
        self.assertEqual(patient_pseudo_id(" synth-mrn-0001 ", KEY), patient_pseudo_id("SYNTH-MRN-0001", KEY))

    def test_key_changes_the_id(self):
        self.assertNotEqual(patient_pseudo_id("SYNTH-MRN-0001", KEY), patient_pseudo_id("SYNTH-MRN-0001", KEY + b"x"))

    def test_encounter_prefix(self):
        self.assertRegex(encounter_pseudo_id("enc-0001", KEY), EID_PATTERN)

    def test_short_key_refused(self):
        with self.assertRaises(ValueError):
            patient_pseudo_id("SYNTH-MRN-0001", b"short")


if __name__ == "__main__":
    unittest.main()
