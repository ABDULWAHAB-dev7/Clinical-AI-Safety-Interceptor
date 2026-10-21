"""Remote NLI scorer: the same contract as the local one, over HTTP to a self-hosted inference service.

The local scorer needs torch and the approved checkpoint in the same process. A serverless function cannot hold
either, so the interceptor calls an inference service instead. That service is part of the deployment, not a
third party: the premise and the claim stay inside the operator's own network, which is what the Gate G1 memo
requires ("a hosted endpoint would send the FHIR-derived premise text and the claim to a third party on every
NLI call. The plan does not allow that path").

This adapter satisfies the same protocol as interceptor.nli.scorer.NLIScorer, so core/verify.py cannot tell the
difference between a local model and a remote one.

Fail-closed. Every transport, protocol or model failure raises RemoteNliError. core/verify.py turns that into a
VerificationError, which is hard-stop rule R2, so the document is blocked and nothing is released. There is no
retry with a different answer and no fallback to a default score: a claim that cannot be scored is not a claim
that is fine.
"""

import math
from collections.abc import Mapping
from typing import Optional

from interceptor.core.errors import ErrorClass, PipelineError

LABELS = ("entailment", "neutral", "contradiction")
MAX_PAIRS = 64  # one request never carries more than this many pairs


class RemoteNliError(Exception):
    """The inference service did not answer with usable scores. The caller must fail closed."""


class RemoteNliScorer:
    """Scores premise/hypothesis pairs with a self-hosted NLI inference service."""

    def __init__(self, endpoint, *, timeout_seconds=5.0, token=None, client=None, path="/v1/score"):
        if not endpoint:
            raise ValueError("the NLI service endpoint is required")
        self.endpoint = str(endpoint).rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.token = token
        self.path = path
        self._client = client
        self._owns_client = client is None

    def _http(self):
        if self._client is None:
            import httpx  # imported here so a deployment that never uses the remote mode does not need it loaded

            self._client = httpx.Client(timeout=self.timeout_seconds, follow_redirects=False)
        return self._client

    def score(self, premise, hypothesis):
        return self.score_batch([(premise, hypothesis)])[0]

    def score_batch(self, pairs):
        pairs = list(pairs)
        if not pairs:
            return []
        if len(pairs) > MAX_PAIRS:
            raise RemoteNliError(f"the NLI service takes at most {MAX_PAIRS} pairs per request")
        body = self._post({"pairs": [[str(p), str(h)] for p, h in pairs]})
        scores = body.get("scores")
        if not isinstance(scores, list) or len(scores) != len(pairs):
            raise RemoteNliError("the NLI service returned the wrong number of scores")
        return [self._one(row) for row in scores]

    def warm_up(self):
        """The same identical-sentence check the local scorer does, so a broken service fails at start-up."""
        try:
            scores = self.score("The patient has a fever.", "The patient has a fever.")
        except RemoteNliError as exc:
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, f"NLI service is not usable: {exc}") from exc
        if max(scores, key=scores.get) != "entailment":
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "NLI service failed its identical-sentence check")

    def _one(self, row) -> Mapping[str, float]:
        if not isinstance(row, dict):
            raise RemoteNliError("the NLI service returned a score that is not an object")
        try:
            values = {label: float(row[label]) for label in LABELS}
        except (KeyError, TypeError, ValueError) as exc:
            raise RemoteNliError("the NLI service returned a score without the three labels") from exc
        for value in values.values():
            if math.isnan(value) or math.isinf(value) or not 0.0 <= value <= 1.0:
                raise RemoteNliError("the NLI service returned a score outside 0 to 1")
        return values

    def _post(self, payload) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self._http().post(self.endpoint + self.path, json=payload, headers=headers,
                                         timeout=self.timeout_seconds)
        except Exception as exc:  # any transport failure, including a timeout
            raise RemoteNliError(f"the NLI service could not be reached: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise RemoteNliError(f"the NLI service answered with HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise RemoteNliError("the NLI service answered with a body that is not JSON") from exc
        if not isinstance(body, dict):
            raise RemoteNliError("the NLI service answered with a body that is not an object")
        return body

    def close(self):
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None


def build_scorer(endpoint: Optional[str], *, timeout_seconds: float, token: Optional[str] = None) -> RemoteNliScorer:
    """Builds a remote scorer and refuses to build one that is not usable."""
    if not endpoint:
        raise RuntimeError("INTERCEPTOR_NLI_MODE=remote needs INTERCEPTOR_NLI_ENDPOINT")
    scorer = RemoteNliScorer(endpoint, timeout_seconds=timeout_seconds, token=token)
    scorer.warm_up()  # a service that cannot answer is a start-up failure, not a per-request failure
    return scorer
