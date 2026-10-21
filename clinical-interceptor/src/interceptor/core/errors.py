"""Typed error taxonomy (architecture section 7.1). Stages raise PipelineError; the pipeline maps it to a fail-mode outcome."""

from enum import Enum


class ErrorClass(str, Enum):
    DECOMPOSITION_SCHEMA_INVALID = "DECOMPOSITION_SCHEMA_INVALID"
    DECOMPOSITION_TIMEOUT = "DECOMPOSITION_TIMEOUT"
    RETRIEVAL_UNAVAILABLE = "RETRIEVAL_UNAVAILABLE"
    RETRIEVAL_PARTIAL = "RETRIEVAL_PARTIAL"
    FHIR_PARSE_FAILURE = "FHIR_PARSE_FAILURE"
    NLI_UNAVAILABLE = "NLI_UNAVAILABLE"
    VERIFICATION_TIMEOUT = "VERIFICATION_TIMEOUT"
    UNIT_NOT_CONVERTIBLE = "UNIT_NOT_CONVERTIBLE"
    POLICY_LOAD_FAILURE = "POLICY_LOAD_FAILURE"
    AUDIT_WRITE_FAILURE = "AUDIT_WRITE_FAILURE"
    UNHANDLED_EXCEPTION = "UNHANDLED_EXCEPTION"


class PipelineError(Exception):
    def __init__(self, error_class: ErrorClass, detail: str = "") -> None:
        super().__init__(f"{error_class.value}: {detail}" if detail else error_class.value)
        self.error_class = error_class
        self.detail = detail
