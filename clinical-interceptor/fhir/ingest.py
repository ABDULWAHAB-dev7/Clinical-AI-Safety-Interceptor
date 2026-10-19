"""Ingestion rules for FHIR resources (DATABASE.md section 1.1).

R1  A resource whose subject/patient reference does not resolve to a Patient in the same batch is rejected.
R2  Codes outside SNOMED CT, LOINC and RxNorm are kept as display text and flagged unmapped.
R3  entered-in-error observations and refuted conditions are stored but excluded from the verification context.
R4  Datetimes are timezone-aware UTC. Naive datetimes are rejected (see models.ResourceBase).

The rule text for R2 and R4 was partly lost in the PDF extraction; the mapping above follows the surviving text.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .models import AllergyIntolerance, Condition, Encounter, Observation, Patient

MAPPED_SYSTEMS = {
    "http://snomed.info/sct",
    "http://loinc.org",
    "http://www.nlm.nih.gov/research/umls/rxnorm",
}

RESOURCE_FILES = {
    "Patient": ("patients.ndjson", Patient),
    "Condition": ("conditions.ndjson", Condition),
    "Observation": ("observations.ndjson", Observation),
    "AllergyIntolerance": ("allergies.ndjson", AllergyIntolerance),
    "Encounter": ("encounters.ndjson", Encounter),
}


@dataclass
class IngestResult:
    resources: dict = field(default_factory=dict)  # (type, id) -> model instance, stored
    rejected: list = field(default_factory=list)  # (type, id, reason)
    excluded_from_context: set = field(default_factory=set)  # (type, id), stored but not verified against
    unmapped: set = field(default_factory=set)  # (type, id), codes outside the three systems

    def ids(self, rtype):
        return {rid for (t, rid) in self.resources if t == rtype}


def load_ndjson(seed_dir):
    """Reads seed/fhir/*.ndjson. Each line is one FHIR resource; the file determines its type."""
    seed_dir = Path(seed_dir)
    raw = []
    for rtype, (filename, _) in RESOURCE_FILES.items():
        path = seed_dir / filename
        if not path.exists():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip():
                raw.append((rtype, f"{filename}:{line_no}", json.loads(line)))
    return raw


def ingest(raw_resources):
    """raw_resources: iterable of (declared_type, source_label, dict)."""
    result = IngestResult()
    candidates = []
    for declared, label, data in raw_resources:
        if not isinstance(data, dict) or data.get("resourceType") != declared:
            result.rejected.append((declared, label, "resourceType does not match the file"))
            continue
        if "patient_pseudo_id" in data:
            result.rejected.append((declared, data.get("id", label), "patient_pseudo_id must not come from FHIR"))
            continue
        model_cls = RESOURCE_FILES[declared][1]
        try:
            model = model_cls.model_validate(data)
        except ValidationError as exc:
            result.rejected.append((declared, data.get("id", label), f"validation: {exc.error_count()} error(s)"))
            continue
        candidates.append(model)

    patient_refs = {f"Patient/{p.id}" for p in candidates if isinstance(p, Patient)}
    for model in candidates:
        key = (model.resourceType, model.id)
        subject = _patient_reference(model)
        if subject is not None and subject not in patient_refs:  # R1
            result.rejected.append((model.resourceType, model.id, f"{subject} is not a Patient in this batch"))
            continue
        result.resources[key] = model
        if _excluded_from_context(model):  # R3
            result.excluded_from_context.add(key)
        if not _all_codes_mapped(model):  # R2
            result.unmapped.add(key)
    return result


def _patient_reference(model):
    if isinstance(model, Patient):
        return None
    ref = getattr(model, "subject", None) or getattr(model, "patient", None)
    return ref.reference if ref is not None else None


def _excluded_from_context(model):
    if isinstance(model, Observation):
        return model.status == "entered-in-error"
    if isinstance(model, Condition) and model.verificationStatus is not None:
        return any(c.code == "refuted" for c in model.verificationStatus.coding)
    return False


def _all_codes_mapped(model):
    concepts = []
    if isinstance(model, (Observation, Condition)):
        concepts.append(model.code)
    if isinstance(model, AllergyIntolerance) and model.code is not None:
        concepts.append(model.code)
    for concept in concepts:
        for coding in concept.coding:
            if coding.system not in MAPPED_SYSTEMS:
                return False
    return True
