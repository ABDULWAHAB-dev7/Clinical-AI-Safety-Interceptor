"""Terminology helpers for the severity rules. Placeholder mappings for synthetic seed data only.

The real terminology policy (RxNorm ingredient to allergy-class mapping) is not defined in DATABASE.md; these entries
cover the synthetic case-0001 and must be replaced by a governed, versioned lookup before any real use.
"""

from collections.abc import Collection, Iterable
from typing import Optional

from .contracts import CodeSystem

# SNOMED CT codes that denote an allergy or drug-allergy concept.
ALLERGY_CODES = {
    "416098002": "Drug allergy",
    "91936005": "Allergy to penicillin",
}

# RxNorm ingredient -> allergy class. Amoxicillin (723) belongs to the penicillin class.
RXNORM_CLASS = {
    "723": "penicillin",
}

# SNOMED code of an allergy -> allergy class.
ALLERGY_CLASS = {
    "91936005": "penicillin",
}


def entity_kind(system: CodeSystem, code: str, active_condition_codes: Collection[str]) -> Optional[str]:
    """Returns the high-risk kind of an entity, or None. Used by the contradiction rule."""
    if system is CodeSystem.SNOMED_CT and code in ALLERGY_CODES:
        return "allergy"
    if system is CodeSystem.RXNORM:
        return "medication_dose"
    if system is CodeSystem.SNOMED_CT and code in active_condition_codes:
        return "active_diagnosis"
    return None


def matches_allergy(rxnorm_codes: Iterable[str], allergy_codes: Iterable[str]) -> bool:
    """True if any RxNorm ingredient shares an allergy class with any documented allergy code."""
    classes = {RXNORM_CLASS[c] for c in rxnorm_codes if c in RXNORM_CLASS}
    allergy_classes = {ALLERGY_CLASS[c] for c in allergy_codes if c in ALLERGY_CLASS}
    return bool(classes & allergy_classes)
