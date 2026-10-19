"""Non-streaming interception (PLAN 5.5): verify all output, apply the release policy, audit, then respond.

The audit write happens before the result is returned. If it fails, AuditFailure is raised and the caller returns
503 AUDIT_WRITE_FAILED with no content. A response that cannot be verified is BLOCKED (HTTP 200, content null).
"""

import hashlib
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Optional

from interceptor.core.categories import classification_for, explanation, hallucination_category
from interceptor.core.claims import overall_severity
from interceptor.core.contracts import NLIClass, VerificationResult
from interceptor.core.errors import PipelineError
from interceptor.core.pipeline import stage_row
from interceptor.store.records import EventRecord, RequestRecord

from .analysis import Verdict, analyze, verify_unit
from .policy import (
    REDACTED,
    WITHHELD,
    finish_reason,
    marker_for,
    render,
    response_status,
    sentence_action,
)

BLOCK_REASONS = {
    "verification_incomplete": "Part of the output could not be verified, so nothing was released.",
    "critical_block": "The output contains a critical conflict and the policy withholds the whole response.",
    "empty_output": "The upstream model returned no text.",
}


class AuditFailure(Exception):
    """The audit record could not be written. Nothing may be released."""


@dataclass
class InterceptContext:
    request_id: str
    patient_pseudo_id: str
    encounter_pseudo_id: Optional[str]
    source_system: str
    note_type: str
    triples: list
    knowledge: object
    verifier_version: str
    store: object
    nli_model: object = None
    warning_action: str = "ANNOTATE"
    critical_action: str = "REDACT_SENTENCE"
    max_sentence_wait_ms: int = 2000
    include_claims: bool = False
    context_warnings: list = field(default_factory=list)


@dataclass
class Outcome:
    status: str
    content: Optional[str]
    finish_reason: str
    interception: dict
    note_id: str
    trace_id: str


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_with_timeout(unit, ctx):
    """Verifies one unit, giving up after max_sentence_wait_ms. A timeout is UNVERIFIED: it is never a default result."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(verify_unit, unit, ctx.triples, ctx.knowledge, ctx.verifier_version, ctx.nli_model)
        return future.result(timeout=ctx.max_sentence_wait_ms / 1000)
    except FutureTimeout:
        return Verdict("UNVERIFIED", trace={"claim_id": unit.claim["claim_id"] if unit.claim else None,
                                            "branch": "timeout"})
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def build_events(units, verdicts, knowledge):
    """EventRecords for CRITICAL and WARNING results, with the server-set category."""
    events = []
    for unit, verdict in zip(units, verdicts):
        if verdict.result is None or verdict.severity not in ("CRITICAL", "WARNING"):
            continue
        result = VerificationResult(**verdict.result)
        category = hallucination_category(result, unit.claim["claim_text"], unit.claim["entities"], knowledge)
        events.append(EventRecord(
            claim_id=result.claim_id, category=category, risk_severity=verdict.severity,
            nli_class=result.nli_class.value, confidence=round(result.confidence, 3),
            claim_text=f"claim:{category.lower()}", risk_rule=result.risk_rule,
            matched_fhir_ref=result.matched_fhir_ref, doc_char_start=unit.claim["char_start"],
            doc_char_end=unit.claim["char_end"],
        ))
    return events


def redaction_reason(verdict):
    result = verdict.result
    if result is None:
        return "This sentence could not be verified."
    classification = classification_for(NLIClass(result["nli_class"]))
    return explanation(classification, result)


def run_nonstream(text, upstream_finish, ctx):
    started = time.monotonic()
    note_sha = sha256_text(text)
    units = analyze(text, note_sha, ctx.knowledge)
    verdicts = [verify_with_timeout(u, ctx) for u in units]
    actions = [sentence_action(v.severity, ctx.warning_action, ctx.critical_action) for v in verdicts]

    status = response_status(actions)
    block_reason = None
    if not units:
        status, block_reason = "BLOCKED", "empty_output"
    elif status == "BLOCKED":
        unverified = any(v.severity == "UNVERIFIED" for v in verdicts)
        block_reason = "verification_incomplete" if unverified else "critical_block"

    spans = [(u.start, u.end, marker_for(a, v.severity)) for u, v, a in zip(units, verdicts, actions)]
    content = None if status == "BLOCKED" else render(text, spans)
    results = [v.result for v in verdicts if v.result is not None]
    severities = overall_severity(results)
    if any(v.severity == "UNVERIFIED" for v in verdicts) and severities == "SAFE":
        severities = "WARNING"  # unverified output is never reported as safe

    events = build_events(units, verdicts, ctx.knowledge)
    claims = [u.claim for u in units if u.claim is not None]
    stages = [
        stage_row("INGEST", {"document_sha256": note_sha}, {"char_length": len(text)}, "OK"),
        stage_row("RETRIEVE", {"patient": ctx.patient_pseudo_id}, {"triples": len(ctx.triples)}, "OK"),
        stage_row("EXTRACT", {"document_sha256": note_sha},
                  {"sentence_count": len(units), "claim_count": len(claims)}, "OK"),
        stage_row("VERIFY", {"claims": [c["claim_id"] for c in claims]},
                  {"verdicts": dict(Counter(v.severity for v in verdicts)),
                   "claims": [v.trace for v in verdicts if v.trace]}, "OK"),
        stage_row("ATTRIBUTE", {"verdicts": [v.severity for v in verdicts]},
                  {"redacted": sum(1 for a in actions if a in (REDACTED, WITHHELD))}, "OK"),
        stage_row("INTERCEPT", {"actions": actions},
                  {"status": status, "reason": block_reason, "actions": dict(Counter(actions)),
                   "elapsed_ms": int((time.monotonic() - started) * 1000)}, "OK"),
    ]
    record = RequestRecord(
        request_id=ctx.request_id,
        patient_pseudo_id=ctx.patient_pseudo_id,
        encounter_pseudo_id=ctx.encounter_pseudo_id,
        source_system=ctx.source_system,
        note_type=ctx.note_type,
        note_sha256=note_sha,
        char_length=len(text),
        claim_count=len(claims),
        pipeline_status="BLOCKED" if status == "BLOCKED" else "RELEASED",
        stages=tuple(stages),
        events=tuple(events),
    )
    try:
        refs = ctx.store.record(record)
    except PipelineError:
        raise AuditFailure()

    redacted_spans = [
        {"doc_char_start": u.start, "doc_char_end": u.end, "risk_severity": v.severity,
         "claim_id": u.claim["claim_id"] if u.claim else None, "reason": redaction_reason(v)}
        for u, v, a in zip(units, verdicts, actions) if a == REDACTED
    ]
    event_views = [
        {"event_id": refs.event_ids.get(e.claim_id), "claim_id": e.claim_id, "category": e.category,
         "risk_severity": e.risk_severity, "risk_rule": e.risk_rule}
        for e in events
    ]
    interception = {
        "status": status,
        "note_id": refs.note_id,
        "trace_id": refs.trace_ids["INTERCEPT"],
        "overall_risk_severity": severities,
        "redacted_spans": redacted_spans,
        "events": event_views,
        "warnings": list(ctx.context_warnings),
        "claims": [_claim_view(c, refs.note_id) for c in claims] if ctx.include_claims else [],
        "results": results if ctx.include_claims else [],
    }
    if block_reason:
        interception["reason"] = BLOCK_REASONS[block_reason]
    return Outcome(status, content, finish_reason(status, upstream_finish), interception, refs.note_id,
                   refs.trace_ids["INTERCEPT"])


def _claim_view(claim, note_id):
    return {
        "claim_id": claim["claim_id"], "note_id": note_id, "source_sentence": claim["source_sentence"],
        "source_sentence_start": claim["source_sentence_start"], "source_sentence_end": claim["source_sentence_end"],
        "claim_text": claim["claim_text"], "char_start": claim["char_start"], "char_end": claim["char_end"],
        "entities": [{"system": e.system.value, "code": e.code, "display": e.display,
                      "surface_text": e.surface_text, "char_start": e.char_start, "char_end": e.char_end}
                     for e in claim["entities"]],
    }
