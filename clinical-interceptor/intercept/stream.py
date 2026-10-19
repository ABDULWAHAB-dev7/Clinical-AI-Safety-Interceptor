"""Streaming interception (API.md 4.2 and 4.3; PLAN 7.5.2).

Rules this module enforces:
  1. A sentence is released only after its verdict, and only after its audit rows were written. Nothing is sent
     before its verdict, and nothing is released without an audit record.
  2. A sentence whose verification exceeds max_sentence_wait_ms is replaced by [WITHHELD: VERIFICATION_TIMEOUT]; the
     stream continues and finishes REDACTED. It never fails open.
  3. If the verifier or the audit writer fails mid-stream, an `error` event (clinical_impact BLOCKED) is sent, then
     [DONE]. Content already released was audited, but the client is told to discard it.
  4. finish_reason "stop" is never sent after an error event.

At most one sentence is held back: a sentence is verified once the next one has started, or the upstream has finished.
Claim IDs in a stream are derived from a per-stream placeholder hash, because the final text is not known yet; they do
not match IDs derived from the final note hash. The note row gets its real hash when the stream ends.
"""

import asyncio
import time
from collections import Counter

from starlette.concurrency import run_in_threadpool

from api.errors import error_body
from api.sse import DONE, chunk, sse
from core.errors import PipelineError
from core.pipeline import stage_row
from store.records import StageRecord

from .analysis import Verdict, analyze, verify_unit
from .pipeline import build_events, sha256_text
from .upstream import UpstreamError, UpstreamTimeout
from .policy import BLOCK, REDACTED, WITHHELD, finish_reason, marker_for, response_status, sentence_action

SEVERITY_ORDER = {"SAFE": 0, "WARNING": 1, "CRITICAL": 2}


class StreamSession:
    def __init__(self, ctx, *, completion_id, model, created, started):
        self.ctx = ctx
        self.completion_id = completion_id
        self.model = model
        self.created = created
        self.started = started
        self.note_id = None
        self.placeholder_sha = sha256_text(f"stream:{ctx.request_id}")
        self.text = ""
        self.cursor = 0  # end of the last released unit
        self.done_units = 0
        self.actions = []
        self.severities = []
        self.claim_count = 0
        self.upstream_finish = None
        self.failed = False
        self.finished = False

    async def open(self):
        """Creates the note row. Called before the response starts, so a failure is still a JSON error."""
        ctx = self.ctx
        self.note_id = await run_in_threadpool(
            ctx.store.stream_begin,
            patient_pseudo_id=ctx.patient_pseudo_id, encounter_pseudo_id=ctx.encounter_pseudo_id,
            source_system=ctx.source_system, note_type=ctx.note_type, placeholder_sha=self.placeholder_sha)

    def _chunk(self, delta, finish=None):
        return chunk(self.completion_id, self.model, self.created, delta, finish)

    def _error(self, code, message, trace_id=None):
        self.failed = True
        body = error_body(self.ctx.request_id, code, message, trace_id=trace_id)
        return sse("error", body) + DONE

    async def run(self, upstream_events):
        yield self._chunk({"role": "assistant"})
        try:
            async for event in upstream_events:
                self.text += event["delta"]
                self.upstream_finish = event["finish_reason"] or self.upstream_finish
                units = analyze(self.text, self.placeholder_sha, self.ctx.knowledge)
                for unit in units[self.done_units:-1]:  # the last unit may still grow
                    async for out in self._sentence(unit):
                        yield out
                    if self.failed or self.finished:
                        return
        except Exception as exc:  # upstream failure mid-stream: content already released stays audited
            if isinstance(exc, UpstreamTimeout):
                yield self._error("INTERCEPT_UPSTREAM_TIMEOUT", "The upstream model timed out. Discard this response.")
            elif isinstance(exc, UpstreamError):
                yield self._error("INTERCEPT_UPSTREAM_ERROR", "The upstream model failed. Discard this response.")
            else:
                yield self._error("INTERCEPT_INTERNAL_ERROR", "Unexpected interceptor fault. Discard this response.")
            await self._abandon("FAILED")
            return

        units = analyze(self.text, self.placeholder_sha, self.ctx.knowledge)
        for unit in units[self.done_units:]:
            async for out in self._sentence(unit):
                yield out
            if self.failed or self.finished:
                return
        async for out in self._complete(units):
            yield out

    async def _verify(self, unit):
        ctx = self.ctx
        try:
            return await asyncio.wait_for(
                run_in_threadpool(verify_unit, unit, ctx.triples, ctx.knowledge, ctx.verifier_version, ctx.nli_model),
                timeout=ctx.max_sentence_wait_ms / 1000)
        except asyncio.TimeoutError:
            return Verdict("WITHHELD", trace={"claim_id": unit.claim["claim_id"] if unit.claim else None,
                                              "branch": "timeout"})

    async def _sentence(self, unit):
        ctx = self.ctx
        verdict = await self._verify(unit)
        action = sentence_action(verdict.severity, ctx.warning_action, ctx.critical_action)

        if action == BLOCK:
            if verdict.severity == "UNVERIFIED":
                yield self._error("INTERCEPT_VERIFIER_UNAVAILABLE",
                                  "A sentence could not be verified. Discard this response.")
                await self._abandon("BLOCKED")
            else:  # CRITICAL with critical_action BLOCK_RESPONSE: stop here, report BLOCKED
                self.actions.append(action)
                self.severities.append(verdict.severity)
                async for out in self._complete([], blocked=True):
                    yield out
            return

        events = build_events([unit], [verdict], ctx.knowledge)
        stage = stage_row("VERIFY", {"claim_ids": [unit.claim["claim_id"]] if unit.claim else []},
                          {"sentence_index": unit.index, "severity": verdict.severity,
                           "claims": [verdict.trace] if verdict.trace else []}, "OK")
        try:
            await run_in_threadpool(ctx.store.stream_sentence, note_id=self.note_id, stage=stage, events=events)
        except PipelineError:
            yield self._error("AUDIT_WRITE_FAILED", "The audit store is unavailable. Discard this response.")
            return

        released_as = "REDACTED" if action in (REDACTED, WITHHELD) else action
        claim_ids = [unit.claim["claim_id"]] if unit.claim else []
        shown_severity = "CRITICAL" if verdict.severity == "WITHHELD" else verdict.severity
        yield sse("interception", {
            "type": "sentence.verified", "sentence_index": unit.index, "doc_char_start": unit.start,
            "doc_char_end": unit.end, "claim_ids": claim_ids, "risk_severity": shown_severity,
            "action": released_as})
        body = marker_for(action, verdict.severity) or self.text[unit.start:unit.end]
        yield self._chunk({"content": self.text[self.cursor:unit.start] + body})

        self.cursor = unit.end
        self.done_units = unit.index + 1
        self.actions.append(action)
        self.severities.append(verdict.severity)
        self.claim_count += 1 if unit.claim else 0

    async def _complete(self, units, blocked=False):
        ctx = self.ctx
        status = "BLOCKED" if blocked else response_status(self.actions)
        overall = max((s for s in self.severities if s in SEVERITY_ORDER), key=SEVERITY_ORDER.get, default="SAFE")
        elapsed = int((time.monotonic() - self.started) * 1000)
        stage = stage_row("INTERCEPT", {"actions": self.actions},
                          {"status": status, "actions": dict(Counter(self.actions)), "elapsed_ms": elapsed}, "OK")
        try:
            trace_id = await run_in_threadpool(
                ctx.store.stream_finish, note_id=self.note_id, note_sha256=sha256_text(self.text),
                char_length=len(self.text), claim_count=self.claim_count,
                pipeline_status="BLOCKED" if blocked else "RELEASED", stage=stage)
        except PipelineError:
            yield self._error("AUDIT_WRITE_FAILED", "The audit store is unavailable. Discard this response.")
            return
        self.finished = True
        reason = finish_reason(status, self.upstream_finish)
        yield self._chunk({}, reason)
        yield sse("interception", {
            "type": "stream.complete", "status": status, "note_id": self.note_id, "trace_id": trace_id,
            "overall_risk_severity": overall, "interception_time_ms": elapsed})
        yield DONE

    async def _abandon(self, pipeline_status):
        """Best effort: record that the stream ended without completing. The client has already been told."""
        stage = stage_row("INTERCEPT", {"actions": self.actions}, {"status": "ABORTED"}, "OK")
        try:
            await run_in_threadpool(
                self.ctx.store.stream_finish, note_id=self.note_id, note_sha256=sha256_text(self.text),
                char_length=len(self.text), claim_count=self.claim_count, pipeline_status=pipeline_status, stage=stage)
        except PipelineError:
            pass
