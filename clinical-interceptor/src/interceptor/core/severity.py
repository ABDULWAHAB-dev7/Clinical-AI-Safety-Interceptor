"""Deterministic severity policy (DATABASE.md section 2.4).

The NLI class is never changed here. Only risk_severity and risk_rule are set. Rules are evaluated in priority order,
and the first match wins.
"""

from dataclasses import dataclass
from typing import Optional

from .contracts import NLIClass, RiskSeverity

ENTAILMENT_CONFIDENCE_FLOOR = 0.85  # DATABASE.md 2.4; calibration is an open item (section 0)
CONTRADICTION_CRITICAL_MIN = 0.80  # policy change 2026-10-20: NLI contradictions block only above this probability
HIGH_RISK_KINDS = frozenset({"allergy", "medication_dose", "active_diagnosis"})


@dataclass(frozen=True)
class ClaimFacts:
    nli_class: NLIClass
    confidence: float
    is_clinical: bool
    is_medication_claim: bool
    matches_allergy: bool  # same RxNorm ingredient or SNOMED class as a documented AllergyIntolerance
    entity_kinds: frozenset[str] = frozenset()


def classify_risk(facts: ClaimFacts, entailment_floor=ENTAILMENT_CONFIDENCE_FLOOR,
                  contradiction_critical_min=CONTRADICTION_CRITICAL_MIN) -> tuple[RiskSeverity, Optional[str]]:
    """Deterministic rule violations (confidence 1.0) block. An NLI contradiction blocks only in a high-risk class and
    only above contradiction_critical_min; otherwise it is a warning (policy change record 2026-10-20)."""
    if facts.is_medication_claim and facts.matches_allergy:  # priority 1
        return RiskSeverity.CRITICAL, "drug_allergy_conflict"
    if (facts.nli_class is NLIClass.CONTRADICTION and facts.entity_kinds & HIGH_RISK_KINDS
            and facts.confidence > contradiction_critical_min):  # priority 2
        return RiskSeverity.CRITICAL, "contradiction_high_risk"
    if facts.nli_class is NLIClass.CONTRADICTION:  # priority 3
        return RiskSeverity.WARNING, "contradiction"
    if facts.nli_class is NLIClass.NEUTRAL and facts.is_clinical:  # priority 4
        return RiskSeverity.WARNING, "unsupported_clinical"
    if facts.nli_class is NLIClass.ENTAILMENT:  # priorities 5 and 6
        if facts.confidence >= entailment_floor:
            return RiskSeverity.SAFE, "entailed"
        return RiskSeverity.WARNING, "low_confidence_entailment"
    return RiskSeverity.SAFE, None  # non-clinical neutral text: no rule changes the severity
