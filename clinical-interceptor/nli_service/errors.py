"""The error type the NLI service uses.

The inference service is built as a container whose build context is the nli_service directory, so it cannot
import from the interceptor package. It therefore carries its own small error type, and the interceptor's
`interceptor.nli.scorer` translates it into the interceptor's taxonomy so nothing else has to change.
"""

from enum import Enum


class ErrorClass(str, Enum):
    POLICY_LOAD_FAILURE = "POLICY_LOAD_FAILURE"


class PipelineError(Exception):
    def __init__(self, error_class: ErrorClass, detail: str = "") -> None:
        super().__init__(f"{error_class.value}: {detail}" if detail else error_class.value)
        self.error_class = error_class
        self.detail = detail
