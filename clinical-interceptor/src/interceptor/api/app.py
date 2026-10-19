"""HTTP application factory (API.md v1.0.0).

Configuration (environment, read by build_default_app; the service refuses to start without the first three):
  INTERCEPTOR_DATABASE_URL   PostgreSQL DSN for the audit schema (db/schema.sql must already be applied)
  INTERCEPTOR_PSEUDO_KEY     secret for pseudo-IDs (at least 16 bytes; lives in a KMS)
  INTERCEPTOR_JWT_PUBLIC_KEY_FILE  PEM public key for bearer tokens. If unset, every token is refused.
  INTERCEPTOR_MTLS_REGISTRY        JSON file of registered client certificates. If unset, every clinical request is refused.
  INTERCEPTOR_TRUSTED_PROXIES      comma-separated addresses of the reverse proxy that forwards the certificate fingerprint
  INTERCEPTOR_PROXY_SECRET         shared secret the proxy sends with the fingerprint (required with trusted proxies)
  INTERCEPTOR_UPSTREAM_BASE_URL    OpenAI-compatible upstream (optional; without it the proxy endpoint refuses)
  INTERCEPTOR_UPSTREAM_API_KEY     upstream credential (never logged)
  INTERCEPTOR_MODEL_ROUTES         JSON map of logical model name to provider model name
  INTERCEPTOR_RATE_LIMIT_PER_MINUTE  per-client limit (default 60)
  INTERCEPTOR_NLI_MODEL_PATH       local NLI model directory (optional; the checkpoint choice is Gate G1)
  INTERCEPTOR_NLI_MODEL_LOCK       JSON file {"sha256": ...} the model directory must match
  INTERCEPTOR_POLICY         policy file (default config/policy/default.yaml)
  INTERCEPTOR_FHIR_DIR       encounter bundles for the legacy /v1/verify endpoint (default fixtures/fhir)
"""

import json
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from interceptor.core.knowledge import load_knowledge
from interceptor.core.pipeline import MAX_DOCUMENT_CHARS, verify_document
from interceptor.core.policy import load_policy
from interceptor.intercept.upstream import UpstreamClient
from interceptor.paths import PROJECT_ROOT

from .auth import DenyAllVerifier, JwtVerifier, require_scope
from .errors import InterceptionError, envelope, register_error_handlers
from .interceptor import VERIFIER_VERSION
from .interceptor import router as interceptor_router
from .mtls import MtlsConfig
from .proxy import router as proxy_router
from .ratelimit import RateLimiter

ROOT = PROJECT_ROOT
CONTRACT_VERSION = "1.0.0"
MAX_BODY_BYTES = 2 * 1024 * 1024  # API.md 13
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
TIMED_PREFIXES = ("/v1/interceptor/", "/v1/verify", "/v1/chat/")


class VerifyRequest(BaseModel):
    """Legacy full-pipeline endpoint: verifies a document against a stored encounter bundle."""

    model_config = ConfigDict(extra="forbid")

    document_text: str = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)
    doc_type: str = Field(min_length=1, max_length=64)
    encounter_ref: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    source_system: str = Field(default="api", min_length=1, max_length=64)


def create_app(*, policy_path, fhir_dir, store, pseudo_key, verifier=None, nli_model=None, knowledge=None,
               mtls=None, limiter=None, upstream=None):
    policy = load_policy(policy_path)
    knowledge = knowledge if knowledge is not None else load_knowledge()  # refuses to start without knowledge files  # refuses to start without a valid policy (architecture 7.2 #5)
    if not isinstance(pseudo_key, (bytes, bytearray)) or len(pseudo_key) < 16:
        raise ValueError("pseudonymization key must be at least 16 bytes")

    app = FastAPI(title="Clinical Interceptor", version=CONTRACT_VERSION)
    app.state.store = store
    app.state.policy = policy
    app.state.knowledge = knowledge
    app.state.pseudo_key = bytes(pseudo_key)
    app.state.fhir_dir = fhir_dir
    app.state.verifier = verifier if verifier is not None else DenyAllVerifier()  # default: refuse everything
    app.state.nli_model = nli_model
    app.state.upstream = upstream  # None: the proxy endpoint refuses (fail-closed)
    app.state.verifier_version = VERIFIER_VERSION
    app.state.mtls = mtls if mtls is not None else MtlsConfig()  # empty registry: every clinical request is refused
    app.state.limiter = limiter if limiter is not None else RateLimiter(60)
    register_error_handlers(app)

    @app.middleware("http")
    async def contract_headers(request: Request, call_next):
        started = time.monotonic()
        request_id = _request_id(request)
        request.state.request_id = request_id

        if request.method == "POST" and not request.headers.get("content-type", "").lower().startswith(
                "application/json"):
            response = envelope(request, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json.")
        else:
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
                response = envelope(request, "INTERCEPT_BUNDLE_TOO_LARGE", "The request body exceeds 2 MiB.")
            else:
                response = await call_next(request)

        response.headers["X-Request-Id"] = request_id
        rate = getattr(request.state, "ratelimit", None)
        if rate is not None:
            response.headers["RateLimit-Limit"] = str(rate.limit)
            response.headers["RateLimit-Remaining"] = str(rate.remaining)
            response.headers["RateLimit-Reset"] = str(rate.reset_seconds)
        response.headers["X-Interceptor-Version"] = CONTRACT_VERSION
        streaming = response.headers.get("content-type", "").startswith("text/event-stream")
        if request.url.path.startswith(TIMED_PREFIXES) and not streaming:  # a stream's total time is in stream.complete
            response.headers["X-Interception-Time-MS"] = str(int((time.monotonic() - started) * 1000))
        return response

    @app.get("/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/health/ready")
    def ready():
        database_ok = store.ping()
        body = {
            "ready": database_ok,
            "components": {
                "database": "ok" if database_ok else "unreachable",
                "verifier": "nli_available" if nli_model is not None else "deterministic_only",
            },
            "policy_version": policy.version,
        }
        return JSONResponse(body, status_code=200 if database_ok else 503)

    app.include_router(interceptor_router)
    app.include_router(proxy_router)

    @app.post("/v1/verify", dependencies=[require_scope("verify")])
    def verify(body: VerifyRequest, request: Request):
        # This endpoint finds the patient from the stored bundle, so a patient-restricted client cannot be checked here.
        if not app.state.mtls.is_unrestricted(request.state.client_id):
            raise InterceptionError("AUTH_SCOPE_INSUFFICIENT", "This endpoint needs a client with access to all patients.")
        if body.doc_type not in policy.doc_types_in_scope:
            raise HTTPException(status_code=400, detail="document type is not in facility scope")
        result = verify_document(
            body.document_text,
            body.doc_type,
            body.encounter_ref,
            policy=policy,
            fhir_dir=fhir_dir,
            store=store,
            pseudo_key=app.state.pseudo_key,
            nli_model=nli_model,
            source_system=body.source_system,
        )
        return _render(result)

    return app


def _request_id(request):
    supplied = request.headers.get("x-request-id", "")
    return supplied if UUID_RE.match(supplied) else str(uuid.uuid4())


def _render(result):
    payload = {
        "request_id": result.request_id,
        "outcome": result.outcome.value,
        "delivered": result.delivered,
        "pipeline_status": result.pipeline_status,
        "policy_version": result.decision.policy_version,
        "rules_fired": result.decision.rules_fired,
        "errors": result.errors,
        "verdicts": [r.model_dump(mode="json") for r in result.records],
    }
    if result.delivered:  # blocked documents return no text (architecture 6.2)
        payload["spans"] = [s.model_dump(mode="json") for s in result.spans]
    return payload


def _load_verifier():
    path = os.environ.get("INTERCEPTOR_JWT_PUBLIC_KEY_FILE")
    if not path:
        return None  # DenyAllVerifier is installed: every clinical request is refused
    return JwtVerifier(Path(path).read_text(encoding="utf-8"))


def _load_mtls():
    """Client registry, trusted proxy addresses and shared secret. With no registry every clinical request is refused."""
    registry = os.environ.get("INTERCEPTOR_MTLS_REGISTRY")
    if not registry:
        return None
    proxies = [p.strip() for p in os.environ.get("INTERCEPTOR_TRUSTED_PROXIES", "").split(",") if p.strip()]
    secret = os.environ.get("INTERCEPTOR_PROXY_SECRET")
    if proxies and not secret:
        raise RuntimeError("INTERCEPTOR_PROXY_SECRET is required when INTERCEPTOR_TRUSTED_PROXIES is set")
    return MtlsConfig.from_registry_file(registry, proxies, secret.encode("utf-8") if secret else None)


def _load_upstream():
    """The upstream model, or None (the proxy endpoint then refuses). Routes: {"logical-name": "provider-model"}."""
    base = os.environ.get("INTERCEPTOR_UPSTREAM_BASE_URL")
    if not base:
        return None
    routes = json.loads(os.environ.get("INTERCEPTOR_MODEL_ROUTES", "{}"))
    if not routes:
        raise RuntimeError("INTERCEPTOR_MODEL_ROUTES is required when INTERCEPTOR_UPSTREAM_BASE_URL is set")
    return UpstreamClient(base, os.environ.get("INTERCEPTOR_UPSTREAM_API_KEY"), routes)


def _load_nli():
    """The local NLI model, if configured. A configured model that fails to load or warm up stops the service."""
    path = os.environ.get("INTERCEPTOR_NLI_MODEL_PATH")
    if not path:
        return None  # claims that need the model then fail closed
    from interceptor.nli.scorer import NLIScorer

    scorer = NLIScorer(path, lock_path=os.environ.get("INTERCEPTOR_NLI_MODEL_LOCK"))
    scorer.warm_up()
    return scorer


def build_default_app():
    """Uvicorn factory: `uvicorn api.app:build_default_app --factory --host 127.0.0.1`."""
    from interceptor.store.postgres import PostgresAuditStore

    dsn = os.environ.get("INTERCEPTOR_DATABASE_URL")
    key = os.environ.get("INTERCEPTOR_PSEUDO_KEY")
    if not dsn:
        raise RuntimeError("INTERCEPTOR_DATABASE_URL is required")
    if not key:
        raise RuntimeError("INTERCEPTOR_PSEUDO_KEY is required")
    return create_app(
        policy_path=os.environ.get("INTERCEPTOR_POLICY", str(ROOT / "config/policy/default.yaml")),
        fhir_dir=os.environ.get("INTERCEPTOR_FHIR_DIR", str(ROOT / "fixtures/fhir")),
        store=PostgresAuditStore(dsn),
        pseudo_key=key.encode("utf-8"),
        verifier=_load_verifier(),
        mtls=_load_mtls(),
        limiter=RateLimiter(int(os.environ.get("INTERCEPTOR_RATE_LIMIT_PER_MINUTE", "60"))),
        upstream=_load_upstream(),
        nli_model=_load_nli(),
    )
