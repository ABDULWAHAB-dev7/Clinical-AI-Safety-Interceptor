"""NLI inference service: the approved checkpoint behind a small HTTP API.

The verifier needs an NLI scorer for the claims the deterministic rules cannot settle. When the interceptor runs
in a serverless function it cannot hold torch or the checkpoint, so it calls this service instead.

This service is part of the deployment, not a third party. The premise (derived from the patient record) and the
claim (text from the note) stay inside the operator's own network, which is the constraint the Gate G1 memo sets:
a *hosted* endpoint operated by someone else would send them off site on every call, and that path is not allowed.

The model is the one Gate G1 approved, `cross-encoder/nli-deberta-v3-base`, and the lock file is checked when the
service starts, so a service that cannot prove it is running the approved weights refuses to start.

Run it with:

    NLI_MODEL_PATH=models/nli/cross-encoder-nli-deberta-v3-base \\
    NLI_MODEL_LOCK=models/nli/MODEL.lock \\
    python -m uvicorn nli_service.app:app --host 127.0.0.1 --port 8100
"""

import os
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from interceptor.core.errors import PipelineError
from interceptor.nli.scorer import NLIScorer

MODEL_PATH_ENV = "NLI_MODEL_PATH"
LOCK_PATH_ENV = "NLI_MODEL_LOCK"
TOKEN_ENV = "NLI_SERVICE_TOKEN"  # noqa: S105 - the name of an environment variable, not a secret
MAX_PAIRS = int(os.environ.get("NLI_MAX_PAIRS", "64"))
MAX_TEXT_CHARS = int(os.environ.get("NLI_MAX_TEXT_CHARS", "4000"))

# The premise and the claim are clinical text. Nothing here logs them.
LABELS = ("entailment", "neutral", "contradiction")


class ScoreRequest(BaseModel):
    """One or more premise/hypothesis pairs to score."""

    model_config = ConfigDict(extra="forbid")

    pairs: list[list[str]] = Field(min_length=1, max_length=MAX_PAIRS)


class ScoreResponse(BaseModel):
    scores: list[dict[str, float]]


class HealthResponse(BaseModel):
    status: str
    model: Optional[str]
    lock_verified: bool
    labels: list[str]


class _Scorer:
    """The model, plus the lock that serialises the forward passes.

    torch releases the GIL during a forward pass, so two threads can call predict() at once, but they then fight
    over the same CPU threads and both get slower. A lock keeps the latency predictable: one batch at a time,
    each batch as fast as the machine allows.
    """

    def __init__(self, model_path: str, lock_path: Optional[str]):
        self.model_path = model_path
        self.lock_path = lock_path
        self._lock = threading.Lock()
        self._scorer = NLIScorer(model_path, lock_path=lock_path)
        self._scorer.warm_up()  # a model that fails its warm-up check never serves a request
        self.labels = list(LABELS)

    @property
    def lock_verified(self) -> bool:
        return self.lock_path is not None

    def score_batch(self, pairs):
        with self._lock:
            return self._scorer.score_batch(pairs)


def _load_scorer() -> _Scorer:
    model_path = os.environ.get(MODEL_PATH_ENV)
    if not model_path:
        raise RuntimeError(f"{MODEL_PATH_ENV} is required: the service refuses to start without a checkpoint")
    lock_path = os.environ.get(LOCK_PATH_ENV) or None
    try:
        return _Scorer(model_path, lock_path)
    except PipelineError as exc:
        raise RuntimeError(f"the NLI model could not be loaded: {exc}") from exc


def _require_token(authorization: Optional[str]) -> None:
    """The service is usually on a private network; the token is a second lock on the door."""
    expected = os.environ.get(TOKEN_ENV)
    if not expected:
        return
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or token.strip() != expected:
        raise HTTPException(status_code=401, detail="a valid bearer token is required")


def create_app(scorer=None) -> FastAPI:
    """The service. `scorer` is injected by the tests; the real one is loaded at start-up."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # A service that cannot load the approved weights does not start. Failing at start-up is better than
        # failing per request, and it is the fail-closed behaviour the rest of the system expects.
        application.state.scorer = scorer if scorer is not None else _load_scorer()
        yield
        application.state.scorer = None

    application = FastAPI(title="Clinical Interceptor NLI service", version="1.0.0", lifespan=lifespan)
    router = APIRouter()

    def _scorer_of(request: Request):
        loaded = getattr(request.app.state, "scorer", None)
        if loaded is None:
            raise HTTPException(status_code=503, detail="the model is not loaded")
        return loaded

    @router.get("/v1/health", response_model=HealthResponse)
    def health(request: Request):
        loaded = getattr(request.app.state, "scorer", None)
        if loaded is None:
            return HealthResponse(status="starting", model=None, lock_verified=False, labels=[])
        return HealthResponse(status="ok", model=loaded.model_path, lock_verified=loaded.lock_verified,
                              labels=list(loaded.labels))

    @router.post("/v1/score", response_model=ScoreResponse)
    def score(body: ScoreRequest, request: Request, authorization: Optional[str] = Header(default=None)):
        _require_token(authorization)
        pairs = []
        for pair in body.pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise HTTPException(status_code=422, detail="each pair must be [premise, hypothesis]")
            premise, hypothesis = str(pair[0]), str(pair[1])
            if not premise.strip() or not hypothesis.strip():
                raise HTTPException(status_code=422, detail="a premise or a hypothesis is empty")
            if len(premise) > MAX_TEXT_CHARS or len(hypothesis) > MAX_TEXT_CHARS:
                raise HTTPException(status_code=422, detail="a premise or a hypothesis is too long")
            pairs.append((premise, hypothesis))
        try:
            rows = _scorer_of(request).score_batch(pairs)
        except Exception as exc:  # the model failed: the caller must fail closed, and it gets no scores
            raise HTTPException(status_code=503, detail=f"the model could not score the pairs: {type(exc).__name__}")
        return ScoreResponse(scores=[{label: float(row[label]) for label in LABELS} for row in rows])

    application.include_router(router)
    return application


app = create_app()
