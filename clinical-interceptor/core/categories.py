"""Hallucination category (API.md 7.1.2) and attribution helpers. The server sets the category; clients never do.

Evaluated in order, the first match wins:
  1  risk_rule drug_allergy_conflict, or a CONTRADICTION where an entity is an allergy or a drug in an allergy class
        -> DRUG_ALLERGY_CONFLICT
  2  CONTRADICTION where an entity is a medication and the claim states a dose or frequency
        -> MEDICATION_DOSE_MISMATCH
  3  CONTRADICTION where an entity is a diagnosis -> DIAGNOSIS_CONTRADICTION
  4  CONTRADICTION or NEUTRAL where the matched fact is an Observation -> LAB_VALUE_MISMATCH
  5  a date inconsistent with the matched fact -> TEMPORAL_MISMATCH   (NOT evaluated: the audit request does not carry
        the matched fact's date, so this row cannot fire yet)
  6  risk_rule unsupported_clinical -> UNSUPPORTED_CLAIM
  7  anything else -> OTHER

"Entity is a diagnosis" means a SNOMED CT entity that is not an allergy concept. The audit request does not say whether
the diagnosis is active, so this is an approximation.
"""

from .contracts import AttributionClass, CodeSystem, NLIClass
from .dosage import parse_dose


def hallucination_category(result, claim_text, entities, knowledge):
    contradiction = result.nli_class is NLIClass.CONTRADICTION
    rx = [e for e in entities if e.system is CodeSystem.RXNORM]
    allergy_concept = any(
        e.system is CodeSystem.SNOMED_CT and e.code in knowledge.allergy_concept_codes for e in entities
    )
    allergy_classes = set(knowledge.allergy_classes.values())
    allergy_class_drug = any(knowledge.ingredient_classes.get(e.code) in allergy_classes for e in rx)

    if result.risk_rule == "drug_allergy_conflict" or (contradiction and (allergy_concept or allergy_class_drug)):
        return "DRUG_ALLERGY_CONFLICT"
    dose, per_day = parse_dose(claim_text)
    if contradiction and rx and (dose is not None or per_day is not None):
        return "MEDICATION_DOSE_MISMATCH"
    if contradiction and any(
        e.system is CodeSystem.SNOMED_CT and e.code not in knowledge.allergy_concept_codes for e in entities
    ):
        return "DIAGNOSIS_CONTRADICTION"
    if result.nli_class in (NLIClass.CONTRADICTION, NLIClass.NEUTRAL) and (
        result.matched_fhir_ref or ""
    ).startswith("Observation/"):
        return "LAB_VALUE_MISMATCH"
    if result.risk_rule == "unsupported_clinical":
        return "UNSUPPORTED_CLAIM"
    return "OTHER"


def classification_for(nli_class):
    """Attribution classification generated from a result when the client supplies no spans."""
    return {
        NLIClass.ENTAILMENT: AttributionClass.SOURCE_SUPPORTED,
        NLIClass.CONTRADICTION: AttributionClass.SOURCE_CONTRADICTED,
        NLIClass.NEUTRAL: AttributionClass.NO_SOURCE_EVIDENCE,
    }[nli_class].value


def explanation(classification, result):
    """Server-generated, text-free explanation. Client explanations are not stored (DATABASE.md 3.6)."""
    ref = (result or {}).get("matched_fhir_ref")
    rule = (result or {}).get("risk_rule")
    if classification == AttributionClass.SOURCE_SUPPORTED.value:
        return f"The statement is supported by {ref}." if ref else "The statement is supported by the patient record."
    if classification == AttributionClass.SOURCE_CONTRADICTED.value:
        target = ref or "the patient record"
        return f"The statement conflicts with {target}."
    if classification == AttributionClass.NON_CLINICAL.value:
        return "This text is narrative or administrative and was not checked against the record."
    if rule == "drug_allergy_conflict":
        return f"The statement describes a drug that matches a documented allergy ({ref})."
    return "No supporting evidence for this statement was found in the patient record."
