"""Ground-truth triples (PLAN 2.4): atomic facts from the record, each with a pointer to its source resource.

Facts that ingestion rule R3 excludes (entered-in-error observations, refuted conditions) are never triples.
Patient gender and birth date produce no triple.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from interceptor.core.contracts import CodeSystem

from .models import AllergyIntolerance, Condition, Encounter, Observation

SYSTEM_LABELS = {
    CodeSystem.SNOMED_CT: "SNOMED",
    CodeSystem.LOINC: "LOINC",
    CodeSystem.RXNORM: "RxNorm",
}
KNOWN_SYSTEMS = {s.value: s for s in CodeSystem}


@dataclass(frozen=True)
class Triple:
    subject: str  # Patient/<id>
    predicate: str  # HAS_ALLERGY | HAS_CONDITION | HAS_OBSERVATION | HAS_ENCOUNTER
    object_code: Optional[str]
    object_system: Optional[CodeSystem]
    object_display: str
    value: Optional[str]  # observations only, for example "8.2 %"
    numeric_value: Optional[float]
    unit: Optional[str]
    status: str
    effective: Optional[datetime]
    source_ref: str  # for example AllergyIntolerance/allergy-penicillin-001
    drug_class: Optional[str]  # allergy triples only, from knowledge/drug_class_map.yaml
    polarity: str = "ASSERTED"


def triples_from_ingest(result, knowledge):
    """One triple per usable resource, in a stable order (resource type, then id)."""
    triples = []
    for (rtype, rid), resource in sorted(result.resources.items()):
        if (rtype, rid) in result.excluded_from_context:
            continue
        if isinstance(resource, AllergyIntolerance):
            triples.append(_allergy(resource, knowledge))
        elif isinstance(resource, Condition):
            triples.append(_condition(resource))
        elif isinstance(resource, Observation):
            triples.append(_observation(resource))
        elif isinstance(resource, Encounter):
            triples.append(_encounter(resource))
    return triples


def verbalize(triple: Triple) -> str:
    """Premise text for the NLI model."""
    code = _code_label(triple)
    if triple.predicate == "HAS_ALLERGY":
        return f"Patient has a {triple.status} {triple.object_display.lower()}{code}."
    if triple.predicate == "HAS_CONDITION":
        return f"Patient has {triple.object_display} (status: {triple.status}){code}."
    if triple.predicate == "HAS_OBSERVATION":
        when = f", effective {triple.effective.date().isoformat()}" if triple.effective else ""
        return f"Observation {triple.object_display}: {triple.value} (status: {triple.status}{when}){code}."
    return f"Patient has a {triple.status} encounter of class {triple.object_display}."


def _code_label(triple):
    if triple.object_code and triple.object_system:
        return f" ({SYSTEM_LABELS[triple.object_system]} {triple.object_code})"
    return ""


def _allergy(resource, knowledge):
    system, code, display = _primary(resource.code)
    return Triple(
        subject=_subject(resource.patient),
        predicate="HAS_ALLERGY",
        object_code=code,
        object_system=system,
        object_display=display,
        value=None,
        numeric_value=None,
        unit=None,
        status=_status(resource.verificationStatus) or _status(resource.clinicalStatus) or "unknown",
        effective=resource.onsetDateTime,
        source_ref=f"AllergyIntolerance/{resource.id}",
        drug_class=knowledge.allergy_classes.get(code) if code else None,
    )


def _condition(resource):
    system, code, display = _primary(resource.code)
    return Triple(
        subject=_subject(resource.subject),
        predicate="HAS_CONDITION",
        object_code=code,
        object_system=system,
        object_display=display,
        value=None,
        numeric_value=None,
        unit=None,
        status=_status(resource.clinicalStatus) or "unknown",
        effective=resource.onsetDateTime,
        source_ref=f"Condition/{resource.id}",
        drug_class=None,
    )


def _observation(resource):
    system, code, display = _primary(resource.code)
    quantity = resource.valueQuantity
    unit = (quantity.unit or quantity.code) if quantity else None
    numeric = quantity.value if quantity else None
    value = f"{numeric:g} {unit}" if numeric is not None and unit else (f"{numeric:g}" if numeric is not None else None)
    return Triple(
        subject=_subject(resource.subject),
        predicate="HAS_OBSERVATION",
        object_code=code,
        object_system=system,
        object_display=display,
        value=value,
        numeric_value=numeric,
        unit=unit,
        status=resource.status,
        effective=resource.effectiveDateTime,
        source_ref=f"Observation/{resource.id}",
        drug_class=None,
    )


def _encounter(resource):
    return Triple(
        subject=_subject(resource.subject),
        predicate="HAS_ENCOUNTER",
        object_code=resource.class_.code,
        object_system=None,
        object_display=resource.class_.code or "unknown",
        value=None,
        numeric_value=None,
        unit=None,
        status=resource.status,
        effective=resource.period.start if resource.period else None,
        source_ref=f"Encounter/{resource.id}",
        drug_class=None,
    )


def _subject(reference):
    return reference.reference


def _status(concept):
    if concept is None:
        return None
    for coding in concept.coding:
        if coding.code:
            return coding.code
    return None


def _primary(concept):
    """(system, code, display) of the first coding in SNOMED CT, LOINC or RxNorm. Unmapped codes keep display text only."""
    if concept is None:
        return None, None, "unknown"
    for coding in concept.coding:
        system = KNOWN_SYSTEMS.get(coding.system)
        if system is not None and coding.code:
            return system, coding.code, coding.display or concept.text or coding.code
    first = next((c for c in concept.coding if c.display), None)
    return None, None, (first.display if first else concept.text) or "unknown"
