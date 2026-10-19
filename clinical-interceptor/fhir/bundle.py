"""Stage 3: FHIR R4 context bundle for one encounter (architecture 3.4).

MVP reads a local Bundle JSON fixture. The SMART Backend Services client is not built yet.
Resources that fail parsing are excluded and reported; absence of a resource type is recorded in
`incomplete_types`, never treated as "no data" (architecture 7.2 #3).
"""

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from core.errors import ErrorClass, PipelineError
from core.models import AssertionType

MRN_SYSTEM = "urn:synthetic:mrn"  # placeholder; the real MRN system comes from facility configuration
ENCOUNTER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
RESOURCE_ATTR = {
    "AllergyIntolerance": "allergies",
    "MedicationRequest": "medications",
    "Observation": "observations",
}


class Named(BaseModel):
    id: str
    name: str


class Observation(BaseModel):
    id: str
    name: str
    value: float
    unit: str


class ContextBundle(BaseModel):
    encounter_ref: str
    allergies: list[Named] = []
    medications: list[Named] = []
    observations: list[Observation] = []
    incomplete_types: set[str] = Field(default_factory=set)
    parse_failures: list[str] = []  # resource types with at least one resource that failed parsing
    patient_mrn: Optional[str] = None  # from the Patient resource; pseudonymized before storage

    def hints(self):
        """Lowercase record terms mapped to assertion types. Used only to guide typing, never to decide verdicts."""
        hints = {n.name.lower(): AssertionType.ALLERGY for n in self.allergies}
        hints.update({n.name.lower(): AssertionType.MEDICATION for n in self.medications})
        return hints

    def render_premise(self):
        """Structured-to-text rendering used as the NLI premise (architecture 3.4)."""
        parts = [f"Allergy: {n.name}." for n in self.allergies]
        parts += [f"Medication: {n.name}." for n in self.medications]
        parts += [f"Observation: {o.name} {o.value:g} {o.unit}." for o in self.observations]
        return " ".join(parts)


def load_bundle(encounter_ref, fixture_dir):
    if not ENCOUNTER_RE.match(encounter_ref):
        raise PipelineError(ErrorClass.RETRIEVAL_UNAVAILABLE, "invalid encounter reference")
    path = Path(fixture_dir) / f"{encounter_ref}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise PipelineError(ErrorClass.RETRIEVAL_UNAVAILABLE, "encounter bundle unavailable")
    return parse_bundle(raw, encounter_ref)


def parse_bundle(raw, encounter_ref):
    """Parses a FHIR Bundle dict into a ContextBundle. Used for fixtures and for bundles sent in request bodies."""
    if not isinstance(raw, dict) or raw.get("resourceType") != "Bundle":
        raise PipelineError(ErrorClass.RETRIEVAL_UNAVAILABLE, "response is not a FHIR Bundle")

    bundle = ContextBundle(encounter_ref=encounter_ref)
    seen = set()
    failed = set()
    for entry in raw.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict):
            continue
        rtype = resource.get("resourceType")
        if rtype == "Patient":
            bundle.patient_mrn = _mrn(resource)
            continue
        if rtype not in RESOURCE_ATTR:
            continue  # unknown resource types are ignored (architecture 8.5)
        seen.add(rtype)
        try:
            _add(bundle, rtype, resource)
        except (KeyError, TypeError, ValueError):
            failed.add(rtype)

    for rtype, attr in RESOURCE_ATTR.items():
        if rtype not in seen or rtype in failed:
            bundle.incomplete_types.add(attr)
    bundle.parse_failures = sorted(failed)
    return bundle


def _add(bundle, rtype, resource):
    rid = resource.get("id")
    if not isinstance(rid, str) or not rid:
        raise ValueError("resource has no id")
    if rtype == "AllergyIntolerance":
        bundle.allergies.append(Named(id=rid, name=_display(resource.get("code"))))
    elif rtype == "MedicationRequest":
        bundle.medications.append(Named(id=rid, name=_display(resource.get("medicationCodeableConcept"))))
    else:
        quantity = resource["valueQuantity"]
        unit = quantity.get("unit") or quantity.get("code")
        if not unit:
            raise ValueError("observation has no unit")
        bundle.observations.append(
            Observation(id=rid, name=_display(resource.get("code")), value=float(quantity["value"]), unit=str(unit))
        )


def _display(concept):
    if not isinstance(concept, dict):
        raise ValueError("missing code")
    if concept.get("text"):
        return str(concept["text"])
    for coding in concept.get("coding", []):
        if isinstance(coding, dict) and coding.get("display"):
            return str(coding["display"])
    raise ValueError("no display name")


def _mrn(patient):
    """MRN from the identifier whose system is MRN_SYSTEM. None if absent: the pipeline then fails closed."""
    for identifier in patient.get("identifier", []):
        if isinstance(identifier, dict) and identifier.get("system") == MRN_SYSTEM and identifier.get("value"):
            return str(identifier["value"])
    return None
