"""The NLI scorer, as the interceptor sees it.

The implementation lives in the nli_service package: the inference service is built as a container whose build
context is that directory, so it cannot import from the interceptor package, and the interceptor and the service
must not drift apart. This module re-exports the service's scorer and translates its error type into the
interceptor's taxonomy, so nothing outside this file has to know where the code lives.
"""

from interceptor.core.errors import ErrorClass, PipelineError
from nli_service.errors import PipelineError as ServicePipelineError
from nli_service.scorer import NLIScorer as _NLIScorer
from nli_service.scorer import directory_sha256, file_digests, files_sha256

__all__ = ["NLIScorer", "directory_sha256", "file_digests", "files_sha256", "ErrorClass", "PipelineError"]


class NLIScorer(_NLIScorer):
    """The service's scorer, raising the interceptor's error type."""

    def __init__(self, *args, **kwargs):
        try:
            super().__init__(*args, **kwargs)
        except ServicePipelineError as exc:
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, exc.detail or str(exc)) from exc

    def warm_up(self):
        try:
            super().warm_up()
        except ServicePipelineError as exc:
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, exc.detail or str(exc)) from exc
