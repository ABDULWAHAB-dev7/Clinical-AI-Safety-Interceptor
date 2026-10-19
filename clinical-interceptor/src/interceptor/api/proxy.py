"""POST /v1/chat/completions/intercept (API.md 4.1 and 4.2): a transparent LLM proxy.

Existing OpenAI-style clients connect with a base-URL change. The model's output is verified against the patient's FHIR
record before it is released, redacted, or blocked. The FHIR bundle is used for verification only and is never sent
upstream. A BLOCKED result is HTTP 200 with content null (finish_reason content_filter); HTTP errors mean the
interceptor itself could not run.

Open decisions, see the README: HTTP trailers (Gate G4), and what BLOCK_RESPONSE means mid-stream.
"""

import dataclasses
import time
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from interceptor.core.errors import PipelineError
from interceptor.intercept.pipeline import AuditFailure, InterceptContext, run_nonstream
from interceptor.intercept.stream import StreamSession
from interceptor.intercept.upstream import UpstreamError, UpstreamTimeout

from .auth import authorize_patient, require_scope
from .context import load_patient_context
from .errors import InterceptionError

router = APIRouter(prefix="/v1")
VERIFIER_VERSION = "verifier-proxy"  # replaced by the app with the verifier version at startup


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)


class ClinicalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_pseudo_id: str = Field(pattern=r"^pid_[0-9a-f]{64}$")
    encounter_pseudo_id: Optional[str] = Field(default=None, pattern=r"^eid_[0-9a-f]{64}$")
    source_system: str = Field(min_length=1, max_length=64)
    note_type: str = Field(min_length=1, max_length=64)
    fhir_bundle: dict


class InterceptionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    warning_action: Literal["ANNOTATE", "REDACT_SENTENCE"] = "ANNOTATE"
    critical_action: Literal["REDACT_SENTENCE", "BLOCK_RESPONSE"] = "REDACT_SENTENCE"
    min_confidence_entailment: float = Field(default=0.85, ge=0.5, le=1.0)
    max_sentence_wait_ms: int = Field(default=2000, ge=200, le=10_000)
    include_claims: bool = False


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    stream: bool = False
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=8192)
    clinical_context: ClinicalContext
    interception: InterceptionPolicy = Field(default_factory=InterceptionPolicy)


@router.post("/chat/completions/intercept", dependencies=[require_scope("intercept")])
async def intercept_chat(body: ChatRequest, request: Request):
    state = request.app.state
    upstream = state.upstream
    if upstream is None:
        raise InterceptionError("INTERCEPT_UPSTREAM_ERROR", "No upstream model is configured.")
    if not upstream.knows(body.model):
        raise InterceptionError("VALIDATION_FAILED", "The model is not in the routing table.", "model")

    # Everything that can be rejected is rejected before the upstream is called.
    clinical = body.clinical_context
    authorize_patient(request, clinical.patient_pseudo_id)
    triples, warnings = await run_in_threadpool(
        load_patient_context, clinical.fhir_bundle, clinical.patient_pseudo_id, state,
        "clinical_context.fhir_bundle", "clinical_context.patient_pseudo_id")
    knowledge = state.knowledge
    knowledge = dataclasses.replace(knowledge, thresholds=dataclasses.replace(
        knowledge.thresholds, entailment_min=body.interception.min_confidence_entailment))
    ctx = InterceptContext(
        request_id=request.state.request_id,
        patient_pseudo_id=clinical.patient_pseudo_id,
        encounter_pseudo_id=clinical.encounter_pseudo_id,
        source_system=clinical.source_system,
        note_type=clinical.note_type,
        triples=triples,
        knowledge=knowledge,
        verifier_version=state.verifier_version,
        store=state.store,
        nli_model=state.nli_model,
        warning_action=body.interception.warning_action,
        critical_action=body.interception.critical_action,
        max_sentence_wait_ms=body.interception.max_sentence_wait_ms,
        include_claims=body.interception.include_claims,
        context_warnings=warnings,
    )
    messages = [m.model_dump() for m in body.messages]
    started = time.monotonic()

    if body.stream:
        return await _stream(body, ctx, messages, upstream, started)

    upstream_started = time.monotonic()
    try:
        generated = await upstream.complete(body.model, messages, body.temperature, body.max_tokens)
    except UpstreamTimeout:
        raise InterceptionError("INTERCEPT_UPSTREAM_TIMEOUT", "The upstream model timed out. No content was released.")
    except UpstreamError:
        raise InterceptionError("INTERCEPT_UPSTREAM_ERROR", "The upstream model returned an error. No content was "
                                "released.")
    upstream_ms = int((time.monotonic() - upstream_started) * 1000)

    try:
        outcome = await run_in_threadpool(run_nonstream, generated["text"], generated["finish_reason"], ctx)
    except AuditFailure:
        raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")

    payload = {
        "id": generated["id"] or f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": generated["created"] or int(time.time()),
        "model": body.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": outcome.content},
                     "finish_reason": outcome.finish_reason}],
        "usage": generated["usage"] or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "interception": outcome.interception,
    }
    return JSONResponse(payload, headers={
        "X-Interception-Status": outcome.status,
        "X-Trace-Id": outcome.trace_id,
        "X-Upstream-Time-MS": str(upstream_ms),
    })


async def _stream(body, ctx, messages, upstream, started):
    """Streams the verified output. Errors before the first byte are ordinary JSON errors."""
    events = upstream.stream(body.model, messages, body.temperature, body.max_tokens).__aiter__()
    try:
        first = await events.__anext__()
    except StopAsyncIteration:
        first = None
    except UpstreamTimeout:
        raise InterceptionError("INTERCEPT_UPSTREAM_TIMEOUT", "The upstream model timed out. No content was released.")
    except UpstreamError:
        raise InterceptionError("INTERCEPT_UPSTREAM_ERROR", "The upstream model returned an error. No content was "
                                "released.")

    session = StreamSession(ctx, completion_id=f"chatcmpl-{uuid.uuid4().hex[:12]}", model=body.model,
                            created=int(time.time()), started=started)
    try:
        await session.open()  # the audit writer must work before any content is released
    except PipelineError:
        await events.aclose()
        raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")

    async def upstream_events():
        if first is not None:
            yield first
        async for event in events:
            yield event

    return StreamingResponse(
        session.run(upstream_events()),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
