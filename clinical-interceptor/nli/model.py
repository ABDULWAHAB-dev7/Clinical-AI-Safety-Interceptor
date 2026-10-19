"""NLI verifier interface (architecture 3.5).

The cross-encoder is not built: its checkpoint needs approval and a license review first (architecture 10.1 #1).
With no model injected, NLI-routed assertions raise NLI_UNAVAILABLE, which becomes VERIFICATION_ERROR and blocks
the document. Ambiguous scores never become SUPPORTED.
"""

from typing import Protocol

from core.errors import ErrorClass, PipelineError
from core.models import Verdict


class ScoreModel(Protocol):
    def score(self, premise: str, hypothesis: str) -> dict:
        """Returns {"entailment": p, "neutral": p, "contradiction": p}."""


def verdict_from_scores(scores, entailment_threshold, contradiction_threshold):
    entailment = scores["entailment"]
    contradiction = scores["contradiction"]
    if contradiction >= contradiction_threshold:  # contradiction takes precedence over entailment
        return Verdict.CONTRADICTED
    if entailment >= entailment_threshold:
        return Verdict.SUPPORTED
    return Verdict.UNGROUNDED  # includes the ambiguous band


def nli_verdict(model, premise, hypothesis, policy):
    if model is None:
        raise PipelineError(ErrorClass.NLI_UNAVAILABLE, "NLI model not loaded")
    try:
        scores = model.score(premise, hypothesis)
        return verdict_from_scores(scores, policy.entailment_threshold, policy.contradiction_threshold)
    except Exception as exc:  # inference or score-shape failure: fail closed
        raise PipelineError(ErrorClass.NLI_UNAVAILABLE, type(exc).__name__)
