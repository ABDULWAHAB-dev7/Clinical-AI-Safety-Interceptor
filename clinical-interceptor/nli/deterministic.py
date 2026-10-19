"""Deterministic verifiers (architecture 3.5). Pure: no I/O, no model inference.

verify() returns a Result when a coded match exists, or None to route the assertion to the NLI fallback.
It raises PipelineError when the record cannot support a verdict (missing data, unit not convertible).
"""

import re
from dataclasses import dataclass

from core.errors import ErrorClass, PipelineError
from core.models import AssertionType, EvidenceRef, Verdict

GENERIC_NEGATIVE = re.compile(r"\b(no known|nkda|no drug allerg\w*)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Result:
    verdict: Verdict
    evidence: tuple


def verify(assertion, bundle, tolerance):
    if assertion.type == AssertionType.ALLERGY:
        return _allergy(assertion, bundle)
    if assertion.type == AssertionType.MEDICATION:
        return _medication(assertion, bundle)
    if assertion.type == AssertionType.OBSERVATION_LAB:
        return _lab(assertion, bundle, tolerance)
    return None


def _term_in(term, text):
    return re.search(r"\b" + re.escape(term.lower()) + r"\b", text) is not None


def _ref(resource_type, resource_id, field_path):
    return EvidenceRef(resource_type=resource_type, resource_id=resource_id, field_path=field_path)


def _allergy(assertion, bundle):
    if "allergies" in bundle.incomplete_types:
        raise PipelineError(ErrorClass.RETRIEVAL_PARTIAL, "allergy records incomplete")
    lower = assertion.text.lower()
    hits = [a for a in bundle.allergies if _term_in(a.name, lower)]
    if hits:
        verdict = Verdict.CONTRADICTED if assertion.negated else Verdict.SUPPORTED
        return Result(verdict, tuple(_ref("AllergyIntolerance", a.id, "code.text") for a in hits))
    if assertion.negated and GENERIC_NEGATIVE.search(lower) and bundle.allergies:
        evidence = tuple(_ref("AllergyIntolerance", a.id, "code.text") for a in bundle.allergies)
        return Result(Verdict.CONTRADICTED, evidence)
    return None


def _medication(assertion, bundle):
    if "medications" in bundle.incomplete_types:
        raise PipelineError(ErrorClass.RETRIEVAL_PARTIAL, "medication records incomplete")
    lower = assertion.text.lower()
    hits = [m for m in bundle.medications if _term_in(m.name, lower)]
    if not hits:
        return None
    verdict = Verdict.CONTRADICTED if assertion.negated else Verdict.SUPPORTED
    return Result(verdict, tuple(_ref("MedicationRequest", m.id, "medicationCodeableConcept.text") for m in hits))


def _lab(assertion, bundle, tolerance):
    if "observations" in bundle.incomplete_types:
        raise PipelineError(ErrorClass.RETRIEVAL_PARTIAL, "observation records incomplete")
    if not assertion.name:
        return None
    matches = [o for o in bundle.observations if o.name.lower() == assertion.name.lower()]
    if not matches:
        return None
    if assertion.value is None or not assertion.unit:
        raise PipelineError(ErrorClass.UNIT_NOT_CONVERTIBLE, "draft lab value has no unit")
    obs = matches[0]
    # No conversion table is loaded yet (PRD open question 2), so units must match exactly.
    if obs.unit.lower() != assertion.unit.lower():
        raise PipelineError(ErrorClass.UNIT_NOT_CONVERTIBLE, "no conversion table entry")
    verdict = Verdict.SUPPORTED if abs(assertion.value - obs.value) <= tolerance else Verdict.CONTRADICTED
    return Result(verdict, (_ref("Observation", obs.id, "valueQuantity.value"),))
