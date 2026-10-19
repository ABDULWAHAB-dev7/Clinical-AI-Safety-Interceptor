"""Deterministic severity policy (DATABASE.md section 2.4).

The NLI class is never changed here. Only risk_severity and risk_rule are set. Rules are evaluated in priority order,
and the first match wins.
"""

from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

from .contracts import NLIClass, RiskSeverity

ENTAILMENT_CONFIDENCE_FLOOR = 0.85  # DATABASE.md 2.4; calibration is an open item (section 0)
HIGH_RISK_KINDS = frozenset({"allergy", "medication_dose", "active_diagnosis"})


@dataclass(frozen=True)
class ClaimFacts:
    nli_class: NLIClass
    confidence: float
    is_clinical: bool
    is_medication_claim: bool
    matches_allergy: bool  # same RxNorm ingredient or SNOMED class as a documented AllergyIntolerance
    entity_kinds: FrozenSet[str] = frozenset()


def classify_risk(facts: ClaimFacts) -> Tuple[RiskSeverity, Optional[str]]:
    if facts.is_medication_claim and facts.matches_allergy:  # priority 1
        return RiskSeverity.CRITICAL, "drug_allergy_conflict"
    if facts.nli_class is NLIClass.CONTRADICTION and facts.entity_kinds & HIGH_RISK_KINDS:  # priority 2
        return RiskSeverity.CRITICAL, "contradiction_high_risk"
    if facts.nli_class is NLIClass.CONTRADICTION:  # priority 3
        return RiskSeverity.WARNING, "contradiction"
    if facts.nli_class is NLIClass.NEUTRAL and facts.is_clinical:  # priority 4
        return RiskSeverity.WARNING, "unsupported_clinical"
    if facts.nli_class is NLIClass.ENTAILMENT:  # priorities 5 and 6
        if facts.confidence >= ENTAILMENT_CONFIDENCE_FLOOR:
            return RiskSeverity.SAFE, "entailed"
        return RiskSeverity.WARNING, "low_confidence_entailment"
    return RiskSeverity.SAFE, None  # non-clinical neutral text: no rule changes the severity
