"""Verification data contracts (DATABASE.md section 2). Each contract is serialized to JSON and stored in
audit_traces.output_payload, so these models are the boundary between pipeline stages."""

from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CLAIM_ID_PATTERN = r"^clm_[0-9a-f]{12}$"
REF_PATTERN = r"^[A-Z][A-Za-z]+/[A-Za-z0-9\-\.]{1,64}$"


class CodeSystem(str, Enum):
    RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
    SNOMED_CT = "http://snomed.info/sct"
    LOINC = "http://loinc.org"


class NLIClass(str, Enum):
    ENTAILMENT = "ENTAILMENT"
    NEUTRAL = "NEUTRAL"
    CONTRADICTION = "CONTRADICTION"


class RiskSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    SAFE = "SAFE"


class AttributionClass(str, Enum):
    SOURCE_SUPPORTED = "SOURCE_SUPPORTED"  # span is backed by a FHIR fact
    SOURCE_CONTRADICTED = "SOURCE_CONTRADICTED"  # span conflicts with a FHIR fact
    NO_SOURCE_EVIDENCE = "NO_SOURCE_EVIDENCE"  # clinical claim with no FHIR support
    NON_CLINICAL = "NON_CLINICAL"  # narrative, administrative, or stylistic text


class MedicalEntity(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: CodeSystem
    code: str = Field(min_length=1, max_length=32)
    display: str = Field(min_length=1, max_length=256)
    surface_text: str  # text as written in the note
    char_start: int = Field(ge=0)  # offsets relative to the full note
    char_end: int = Field(ge=0)

    @model_validator(mode="after")
    def _span_valid(self) -> "MedicalEntity":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


class AtomicProposition(BaseModel):
    """One verifiable clinical claim. Offsets are relative to the full note, not the sentence."""

    claim_id: str = Field(pattern=CLAIM_ID_PATTERN)
    note_id: UUID
    source_sentence: str = Field(min_length=1)
    source_sentence_start: int = Field(ge=0)
    source_sentence_end: int = Field(ge=0)
    claim_text: str = Field(min_length=1, max_length=512)  # one normalized fact
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    entities: list[MedicalEntity] = Field(default_factory=list)

    @model_validator(mode="after")
    def _offsets(self) -> "AtomicProposition":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.source_sentence_end <= self.source_sentence_start:
            raise ValueError("source_sentence_end must be greater than source_sentence_start")
        if not (self.source_sentence_start <= self.char_start and self.char_end <= self.source_sentence_end):
            raise ValueError("claim span must lie inside its source sentence")
        return self


class VerificationResult(BaseModel):
    result_id: UUID
    claim_id: str = Field(pattern=CLAIM_ID_PATTERN)
    matched_fhir_ref: Optional[str] = Field(default=None, pattern=REF_PATTERN)
    nli_class: NLIClass
    confidence: float = Field(ge=0.0, le=1.0)
    risk_severity: RiskSeverity
    risk_rule: Optional[str] = Field(
        default=None,
        description="Name of the deterministic rule that set or overrode the severity, e.g. 'drug_allergy_conflict'.",
    )
    verifier_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _consistency(self) -> "VerificationResult":
        # An entailment must point at the FHIR fact that entails it.
        if self.nli_class is NLIClass.ENTAILMENT and self.matched_fhir_ref is None:
            raise ValueError("ENTAILMENT requires matched_fhir_ref")
        # An entailed claim cannot be a critical risk without a rule override.
        if self.nli_class is NLIClass.ENTAILMENT and self.risk_severity is RiskSeverity.CRITICAL and self.risk_rule is None:
            raise ValueError("ENTAILMENT cannot carry CRITICAL severity without a rule override")
        return self


class AttributionSpan(BaseModel):
    """Maps a character range of the source document to a classification and explanation shown to the clinician."""

    span_id: UUID
    note_id: UUID
    doc_char_start: int = Field(ge=0)
    doc_char_end: int = Field(ge=0)
    classification: AttributionClass
    claim_id: Optional[str] = Field(default=None, pattern=CLAIM_ID_PATTERN)
    result_id: Optional[UUID] = None
    explanation: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _range(self) -> "AttributionSpan":
        if self.doc_char_end <= self.doc_char_start:
            raise ValueError("doc_char_end must be greater than doc_char_start")
        return self
