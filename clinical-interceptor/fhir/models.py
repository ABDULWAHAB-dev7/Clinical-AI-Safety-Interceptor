"""FHIR R4 ingestion models (DATABASE.md section 1).

These validate inbound FHIR JSON only. They are not the storage schema. Unknown FHIR fields are ignored so that
extra data cannot break ingestion.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FHIRBase(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Coding(FHIRBase):
    system: Optional[str] = None
    code: Optional[str] = None
    display: Optional[str] = None


class CodeableConcept(FHIRBase):
    coding: list[Coding] = Field(default_factory=list)
    text: Optional[str] = None


class Reference(FHIRBase):
    reference: str = Field(
        pattern=r"^[A-Z][A-Za-z]+/[A-Za-z0-9\-\.]{1,64}$",
        description="Relative FHIR reference, e.g. 'Patient/pat-0001'.",
    )
    display: Optional[str] = None


class Period(FHIRBase):
    start: Optional[datetime] = None
    end: Optional[datetime] = None


class Quantity(FHIRBase):
    value: Optional[float] = None
    unit: Optional[str] = None
    system: Optional[str] = None
    code: Optional[str] = None


class Identifier(FHIRBase):
    system: Optional[str] = None
    value: Optional[str] = None
    use: Optional[Literal["usual", "official", "temp", "secondary", "old"]] = None


class ResourceBase(FHIRBase):
    id: str = Field(min_length=1, max_length=64)
    last_updated: Optional[datetime] = None

    @model_validator(mode="after")
    def _timezone_aware(self) -> "ResourceBase":
        # Rule R4: stored as timezone-aware UTC. Naive datetimes are rejected rather than guessed.
        # Checks top-level datetime fields; nested Period datetimes are not checked yet.
        for name, value in self:
            if isinstance(value, datetime) and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        return self


class HumanName(FHIRBase):
    family: Optional[str] = None
    given: list[str] = Field(default_factory=list)
    use: Optional[Literal["usual", "official", "temp", "nickname", "anonymous", "old", "maiden"]] = None


class Patient(ResourceBase):
    resourceType: Literal["Patient"] = "Patient"
    identifier: list[Identifier] = Field(default_factory=list)
    active: Optional[bool] = None
    name: list[HumanName] = Field(default_factory=list)
    gender: Optional[Literal["male", "female", "other", "unknown"]] = None
    birthDate: Optional[date] = None
    deceasedBoolean: Optional[bool] = None
    deceasedDateTime: Optional[datetime] = None
    # Derived by the pseudonymization service. Never copied from FHIR (see fhir/ingest.py).
    patient_pseudo_id: Optional[str] = Field(default=None, pattern=r"^pid_[0-9a-f]{64}$")


class ReferenceRange(FHIRBase):
    low: Optional[Quantity] = None
    high: Optional[Quantity] = None
    text: Optional[str] = None


class Observation(ResourceBase):
    resourceType: Literal["Observation"] = "Observation"
    status: Literal[
        "registered", "preliminary", "final", "amended",
        "corrected", "cancelled", "entered-in-error", "unknown",
    ]
    category: list[CodeableConcept] = Field(default_factory=list)
    code: CodeableConcept  # LOINC expected in code.coding
    subject: Reference  # Patient/...
    encounter: Optional[Reference] = None
    effectiveDateTime: Optional[datetime] = None
    issued: Optional[datetime] = None
    valueQuantity: Optional[Quantity] = None
    valueString: Optional[str] = None
    valueCodeableConcept: Optional[CodeableConcept] = None
    referenceRange: list[ReferenceRange] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_most_one_value(self) -> "Observation":
        values = [self.valueQuantity, self.valueString, self.valueCodeableConcept]
        if sum(v is not None for v in values) > 1:
            raise ValueError("Observation may carry at most one value[x] element")
        return self


class AllergyReaction(FHIRBase):
    manifestation: list[CodeableConcept] = Field(default_factory=list)
    severity: Optional[Literal["mild", "moderate", "severe"]] = None


class AllergyIntolerance(ResourceBase):
    resourceType: Literal["AllergyIntolerance"] = "AllergyIntolerance"
    clinicalStatus: Optional[CodeableConcept] = None  # active | inactive | resolved
    verificationStatus: Optional[CodeableConcept] = None  # confirmed | unconfirmed | refuted
    type: Optional[Literal["allergy", "intolerance"]] = None
    category: list[Literal["food", "medication", "environment", "biologic"]] = Field(default_factory=list)
    criticality: Optional[Literal["low", "high", "unable-to-assess"]] = None
    code: Optional[CodeableConcept] = None  # SNOMED or RxNorm
    patient: Reference
    onsetDateTime: Optional[datetime] = None
    reaction: list[AllergyReaction] = Field(default_factory=list)


class Condition(ResourceBase):
    resourceType: Literal["Condition"] = "Condition"
    clinicalStatus: Optional[CodeableConcept] = None  # active | recurrence | inactive | remission | resolved
    verificationStatus: Optional[CodeableConcept] = None  # confirmed | provisional | differential | refuted
    category: list[CodeableConcept] = Field(default_factory=list)
    severity: Optional[CodeableConcept] = None
    code: CodeableConcept  # SNOMED CT expected
    subject: Reference
    encounter: Optional[Reference] = None
    onsetDateTime: Optional[datetime] = None
    abatementDateTime: Optional[datetime] = None
    recordedDate: Optional[datetime] = None


class Encounter(ResourceBase):
    resourceType: Literal["Encounter"] = "Encounter"
    status: Literal[
        "planned", "arrived", "triaged", "in-progress", "onleave",
        "finished", "cancelled", "entered-in-error", "unknown",
    ]
    class_: Coding = Field(alias="class")  # v3-ActCode: AMB, IMP, EMER ...
    type: list[CodeableConcept] = Field(default_factory=list)
    subject: Reference
    period: Optional[Period] = None
    reasonCode: list[CodeableConcept] = Field(default_factory=list)
    serviceProvider: Optional[Reference] = None
