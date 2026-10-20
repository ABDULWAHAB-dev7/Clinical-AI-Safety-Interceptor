"""Domain models shared by all stages. Pure data: no I/O imports (architecture section 5)."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AssertionType(str, Enum):
    ALLERGY = "allergy"
    MEDICATION = "medication"
    OBSERVATION_LAB = "observation_lab"
    OBSERVATION_VITAL = "observation_vital"
    CONDITION = "condition"
    NEGATED_FINDING = "negated_finding"
    FINDING_IMAGING = "finding_imaging"
    PLAN = "plan"
    PROCEDURE = "procedure"
    TEMPORAL_EVENT = "temporal_event"
    OTHER_CLINICAL = "other_clinical"


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNGROUNDED = "UNGROUNDED"
    CONTRADICTED = "CONTRADICTED"
    EXCLUDED = "EXCLUDED"
    VERIFICATION_ERROR = "VERIFICATION_ERROR"


class Outcome(str, Enum):
    PASS_THROUGH = "PASS_THROUGH"  # noqa: S105 - an outcome label
    WARN = "WARN"
    HARD_BLOCK = "HARD_BLOCK"


class Assertion(BaseModel):
    model_config = ConfigDict(frozen=True)

    assertion_id: str
    type: AssertionType
    span_start: int
    span_end: int
    text: str  # request-scoped only; never written to the audit store
    negated: bool = False
    name: Optional[str] = None  # subject name, lab assertions only
    value: Optional[float] = None
    unit: Optional[str] = None
    section: Optional[str] = None  # context tag from a section header; never part of the text


class EvidenceRef(BaseModel):
    resource_type: str
    resource_id: str
    field_path: str


class VerdictRecord(BaseModel):
    assertion_id: str
    type: AssertionType
    verdict: Verdict
    verifier: str  # "deterministic" | "nli" | "none"
    evidence: list[EvidenceRef] = []
    error_class: Optional[str] = None
    confidence: Optional[float] = None  # 1.0 for deterministic matches; NLI score otherwise


class SpanRecord(BaseModel):
    start: int
    end: int
    verdict: Verdict
    assertion_ids: list[str]
    evidence: list[EvidenceRef]
    text: str


class Decision(BaseModel):
    outcome: Outcome
    policy_version: str
    rules_evaluated: list[str]
    rules_fired: list[str]
