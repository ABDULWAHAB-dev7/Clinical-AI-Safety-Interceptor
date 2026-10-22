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
import time
from collections.abc import Mapping
from typing import Optional

from interceptor.core.errors import ErrorClass, PipelineError

LABELS = ("entailment", "neutral", "contradiction")
MAX_PAIRS = 64  # one request never carries more than this many pairs
# The platform answers 502, 503 or 504 while a scaled-to-zero container boots. That is the platform talking, not the
# model, so it is retried like an unreachable service. Any other status is an answer and is never retried.
TRANSIENT_STATUS = frozenset({502, 503, 504})


class RemoteNliError(Exception):
    """The inference service did not answer with usable scores. The caller must fail closed."""


class RemoteNliUnavailable(RemoteNliError):
    """The inference service could not be reached at all. It may be down, or still booting."""


class RemoteNliScorer:
    """Scores premise/hypothesis pairs with a self-hosted NLI inference service.

    Two different budgets, because they answer different questions. `timeout_seconds` is how long one HTTP call may
    take: a call that has not answered in ten seconds is not going to. `budget_seconds` is the whole time the
    scorer may spend, retries included, and it is what covers a service that is still booting. Without the
    split, one hung connection would hold the request for the whole budget.
    """

    def __init__(self, endpoint, *, timeout_seconds=10.0, budget_seconds=None, token=None, client=None,
                 path="/v1/score", startup_retries=40, retry_backoff_seconds=1.5):
        if not endpoint:
            raise ValueError("the NLI service endpoint is required")
        self.endpoint = str(endpoint).rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.budget_seconds = float(budget_seconds if budget_seconds is not None else timeout_seconds)
        self.token = token
        self.path = path
        # A service that scales to zero has to boot before it can answer, and booting the model takes longer than
        # one scoring call. A transport failure is retried; a protocol or model failure is not, because that is
        # an answer and an answer must not be second-guessed.
        self.startup_retries = int(startup_retries)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.ready = False  # True once the service has answered correctly at least once
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

    def warm_up(self, budget_seconds=None):
        """The same identical-sentence check the local scorer does, so a broken service fails at start-up.

        A service that cannot be reached is reported as unavailable rather than as broken: it may be down, or it
        may still be booting, and neither is a reason to refuse to start the interceptor. A service that answers
        wrongly is a fault, and that does stop the interceptor.

        `budget_seconds` bounds this one check, so a start-up does not wait the whole per-request budget for a
        service that is still booting. The first clinical request does that waiting instead, and the work it waits
        for has already been started here.
        """
        full_budget = self.budget_seconds
        if budget_seconds is not None:
            self.budget_seconds = min(full_budget, float(budget_seconds))
        try:
            scores = self.score("The patient has a fever.", "The patient has a fever.")
        except RemoteNliUnavailable as exc:
            raise RemoteNliUnavailable(f"the NLI service could not be reached: {exc}") from exc
        except RemoteNliError as exc:
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, f"NLI service is not usable: {exc}") from exc
        finally:
            self.budget_seconds = full_budget
        if max(scores, key=scores.get) != "entailment":
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "NLI service failed its identical-sentence check")
        self.ready = True
        return scores

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
        deadline = time.monotonic() + self.budget_seconds
        attempt = 0
        while True:
            try:
                response = self._http().post(self.endpoint + self.path, json=payload, headers=headers,
                                             timeout=self.timeout_seconds)
                if response.status_code not in TRANSIENT_STATUS:
                    break
                failure, cause = f"HTTP {response.status_code}", None
            except Exception as exc:  # a transport failure: the service may still be booting
                failure, cause = type(exc).__name__, exc
            attempt += 1
            if attempt > self.startup_retries or time.monotonic() >= deadline:
                raise RemoteNliUnavailable(f"the NLI service could not be reached: {failure}") from cause
            time.sleep(min(self.retry_backoff_seconds, max(0.0, deadline - time.monotonic())))
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


def build_scorer(endpoint: Optional[str], *, timeout_seconds: float, budget_seconds: Optional[float] = None,
                 token: Optional[str] = None, startup_retries: int = 40, retry_backoff_seconds: float = 1.5,
                 warmup_budget_seconds: float = 5.0) -> RemoteNliScorer:
    """Builds a remote scorer.

    A service that answers wrongly is a fault and stops the interceptor. A service that cannot be reached is not:
    the interceptor starts, reports the model as unavailable, and every claim that needs the model fails closed
    while the rest of the service keeps working. The interceptor's availability must not depend on the model's.

    The start-up check gets a short budget of its own. A service that is still loading its model has already been
    woken by the attempt, so the first clinical request is the right place to wait for the rest of it: a cold start
    of the interceptor should not sit in front of a cold start of the model.
    """
    if not endpoint:
        raise RuntimeError("INTERCEPTOR_NLI_MODE=remote needs INTERCEPTOR_NLI_ENDPOINT")
    scorer = RemoteNliScorer(endpoint, timeout_seconds=timeout_seconds, budget_seconds=budget_seconds, token=token,
                             startup_retries=startup_retries, retry_backoff_seconds=retry_backoff_seconds)
    try:
        scorer.warm_up(budget_seconds=warmup_budget_seconds)
    except RemoteNliUnavailable:
        # The service is down or still booting. The first clinical request waits for it, and fails closed if it
        # never comes up. The warm-up is not retried for long here, so a cold start of the interceptor is quick.
        scorer.ready = False
        return scorer
    return scorer
