"""The one verification engine (API.md 5 to 7). Every route calls these functions, so all routes decide the same way:

  decompose  split a note into claims, record the note and its EXTRACT trace
  verify     load the patient's record, classify each claim, record the VERIFY trace
  audit      map results to events and attribution spans, write the report and its traces

The HTTP layer (api/interceptor.py, api/app.py) keeps the request checks, idempotency and status codes. It never decides
a verdict itself. Store failures raise InterceptionError with AUDIT_WRITE_FAILED, so no content is released.
"""

import hashlib
import json
import uuid
from datetime import UTC

from interceptor.core.categories import classification_for, explanation, hallucination_category
from interceptor.core.claims import claim_id_for, decompose_claims, overall_severity, summary
from interceptor.core.entities import entity_from_model
from interceptor.core.errors import PipelineError
from interceptor.core.pipeline import PIPELINE_VERSION
from interceptor.core.verify import VerificationError, verify_claims
from interceptor.store.records import EventRecord, StageRecord

from .context import load_patient_context
from .errors import InterceptionError

DECOMPOSER_VERSION = f"decomposer-rules-{PIPELINE_VERSION}"
VERIFIER_VERSION = f"verifier-{PIPELINE_VERSION}+knowledge-seed"


def decompose(state, *, patient_pid, encounter_pid, text, source_system, note_type, include_entities, max_claims,
              idempotency=None):
    """Splits the note and records it. Returns (registration, payload). `idempotency` is kept in the EXTRACT trace."""
    note_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    claims, warnings = decompose_claims(text, note_sha, note_id="", knowledge=state.knowledge,
                                        include_entities=include_entities)
    if len(claims) > max_claims:
        raise InterceptionError("INTERCEPT_CLAIM_LIMIT_EXCEEDED", "The note yields more claims than max_claims.",
                                "options.max_claims")

    output = {
        "assertion_count": len(claims),
        "skipped": len(warnings),
        # an index of IDs and offsets only: claim text is not stored (DATABASE.md 3.6)
        "claims": [{"claim_id": c["claim_id"], "char_start": c["char_start"], "char_end": c["char_end"],
                    "source_sentence_start": c["source_sentence_start"],
                    "source_sentence_end": c["source_sentence_end"]} for c in claims],
    }
    if idempotency is not None:
        output["idempotency"] = idempotency
    extract = StageRecord(
        stage="EXTRACT",
        component_name="decompose-rules",
        component_version=DECOMPOSER_VERSION,
        input_sha256=note_sha,
        output_payload=output,
        status="OK",
    )
    try:
        registration = state.store.register_note(
            patient_pseudo_id=patient_pid,
            encounter_pseudo_id=encounter_pid,
            source_system=source_system,
            note_type=note_type,
            note_sha256=note_sha,
            char_length=len(text),
            extract=extract,
        )
    except PipelineError:
        raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")

    for claim in claims:
        claim["note_id"] = registration.note_id
    payload = {
        "note_id": registration.note_id,
        "patient_pseudo_id": patient_pid,
        "encounter_pseudo_id": encounter_pid,
        "note_sha256": note_sha,
        "char_length": len(text),
        "claim_count": len(claims),
        "decompose_trace_id": registration.decompose_trace_id,
        "component_version": DECOMPOSER_VERSION,
        "warnings": warnings,
        "claims": claims,
    }
    return registration, payload


def verify(state, *, patient_pid, raw_bundle, claims, note_id=None):
    """Classifies each claim against the patient's record in `raw_bundle`. `claims` are objects with claim_id,
    claim_text, char_start and entities (MedicalEntity models). Returns the VERIFY payload."""
    triples, warnings = load_patient_context(raw_bundle, patient_pid, state)

    if note_id is not None:
        owner = state.store.note_patient(note_id)
        if owner is None:
            raise InterceptionError("NOTE_NOT_FOUND", "The note is unknown or not visible to the caller.", "note_id")
        if owner != patient_pid:
            raise InterceptionError("AUDIT_NOTE_PATIENT_MISMATCH", "note_id belongs to a different patient.",
                                    "patient_pseudo_id")

    inputs = [
        {"claim_id": c.claim_id, "claim_text": c.claim_text, "char_start": c.char_start,
         "entities": [entity_from_model(e) for e in c.entities]}
        for c in claims
    ]
    try:
        outcome = verify_claims(inputs, triples, state.knowledge, VERIFIER_VERSION, state.nli_model)
    except VerificationError:
        raise InterceptionError("INTERCEPT_VERIFIER_UNAVAILABLE",
                                "A claim cannot be verified without the NLI verifier. No content was released.")
    results = outcome.results

    verify_trace_id = None
    if note_id is not None:
        stage = StageRecord(
            stage="VERIFY",
            component_name="verifier",
            component_version=VERIFIER_VERSION,
            input_sha256=digest([c["claim_id"] for c in inputs]),
            output_payload={"results": count_by(results, "nli_class"), "claims": outcome.traces},
            status="OK",
        )
        try:
            verify_trace_id = state.store.record_verify(str(note_id), stage)
        except PipelineError:
            raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")

    return {
        "verification_context_sha256": digest(raw_bundle),
        "verifier_version": VERIFIER_VERSION,
        "verify_trace_id": verify_trace_id,
        "overall_risk_severity": overall_severity(results),
        "summary": summary(results),
        "results": results,
        "warnings": warnings,
    }


def audit(state, *, patient_pid, note_id, verify_trace_ids, claims, results, attribution, create_for, body_sha,
          scope):
    """Writes the report. `claims` is a list of AtomicProposition-like objects, `results` a list of VerificationResult
    models, `attribution` an optional list of client spans. Returns the report view (API.md 7.1.4)."""
    store = state.store
    note = _store_call(store.get_note, note_id)
    if note is None:
        raise InterceptionError("NOTE_NOT_FOUND", "The note is unknown or not visible to the caller.", "note_id")
    if note["patient_pseudo_id"] != patient_pid:
        raise InterceptionError("AUDIT_NOTE_PATIENT_MISMATCH", "note_id belongs to a different patient.",
                                "patient_pseudo_id")

    claim_map = {c.claim_id: c for c in claims}
    result_map = {r.claim_id: r for r in results}
    if len(claim_map) != len(claims) or len(result_map) != len(results) or set(claim_map) != set(result_map):
        raise InterceptionError("AUDIT_CLAIM_MISMATCH", "Every claim needs exactly one result, and every result "
                                "a claim.", "results")
    _check_claims_belong_to_note(store, note, note_id, claim_map)
    for trace_id in verify_trace_ids:
        trace = _store_call(store.get_trace, str(trace_id))
        if trace is None or trace["stage"] != "VERIFY" or trace["note_id"] != note_id:
            raise InterceptionError("AUDIT_CLAIM_MISMATCH", "A verify trace does not exist for this note.",
                                    "verify_trace_ids")

    spans = _spans(results, attribution, claim_map, result_map, note, note_id)
    knowledge = state.knowledge
    events = []
    for claim_id, result in result_map.items():
        severity = result.risk_severity.value
        if severity == "SAFE" or severity not in create_for:
            continue
        claim = claim_map[claim_id]
        category = hallucination_category(result, claim.claim_text, [entity_from_model(e) for e in claim.entities],
                                          knowledge)
        events.append(EventRecord(
            claim_id=claim_id, category=category, risk_severity=severity, nli_class=result.nli_class.value,
            confidence=round(result.confidence, 3), claim_text=f"claim:{category.lower()}",
            risk_rule=result.risk_rule, matched_fhir_ref=result.matched_fhir_ref,
            doc_char_start=claim.char_start, doc_char_end=claim.char_end,
        ))

    result_dicts = [r.model_dump(mode="json") for r in results]
    pipeline_status = "BLOCKED" if any(e.risk_severity == "CRITICAL" for e in events) else "VERIFIED"
    report_id = str(uuid.uuid4())
    stage = StageRecord(
        stage="ATTRIBUTE",
        component_name="audit-report",
        component_version=VERIFIER_VERSION,
        input_sha256=body_sha,
        output_payload={
            "report_id": report_id,
            "overall_risk_severity": overall_severity(result_dicts),
            "counts": summary(result_dicts)["by_severity"],
            "pipeline_status": pipeline_status,
            "verify_trace_ids": [str(t) for t in verify_trace_ids],
            "results": result_dicts,
            "attribution": spans,
            "idempotency": {"scope": scope, "body_sha256": body_sha},
        },
        status="OK",
    )
    try:
        refs = store.write_audit_report(note_id=note_id, stage=stage, events=events,
                                        pipeline_status=pipeline_status, claim_count=len(claims))
    except PipelineError:
        raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")

    event_by_claim = {claim_id: (event_id, detected_at) for claim_id, event_id, detected_at in refs.events}
    report = {
        "trace_id": refs.trace_id, "note_id": note_id, "patient_pseudo_id": patient_pid,
        "payload": stage.output_payload, "created_at": refs.created_at,
        "events": [
            {"event_id": event_by_claim[e.claim_id][0], "claim_id": e.claim_id, "category": e.category,
             "risk_severity": e.risk_severity, "nli_class": e.nli_class, "risk_rule": e.risk_rule,
             "confidence": e.confidence, "matched_fhir_ref": e.matched_fhir_ref,
             "detected_at": event_by_claim[e.claim_id][1]}
            for e in sorted(events, key=lambda e: e.claim_id)
        ],
    }
    return report_view(report, replay=False)


def _check_claims_belong_to_note(store, note, note_id, claims):
    index = _store_call(store.get_claim_index, note_id)
    known = {c["claim_id"]: c for c in (index or [])}
    for claim in claims.values():
        recorded = known.get(claim.claim_id)
        derived = claim_id_for(note["note_sha256"], claim.char_start, claim.char_end, claim.claim_text)
        if (str(claim.note_id) != note_id or recorded is None or derived != claim.claim_id
                or (recorded["char_start"], recorded["char_end"]) != (claim.char_start, claim.char_end)
                or claim.char_end > note["char_length"]):
            raise InterceptionError("AUDIT_CLAIM_MISMATCH", "A claim does not belong to this note.", "claims")


def _spans(results, attribution, claims, result_map, note, note_id):
    """Attribution spans, without the explanation text (it is generated again whenever the report is read)."""
    result_ids = {str(r.result_id): r.claim_id for r in results}
    if attribution is None:
        return [
            {"span_id": str(uuid.uuid4()), "doc_char_start": claims[cid].char_start,
             "doc_char_end": claims[cid].char_end, "classification": classification_for(r.nli_class),
             "claim_id": cid, "result_id": str(r.result_id)}
            for cid, r in sorted(result_map.items())
        ]
    spans = []
    for span in attribution:
        if (str(span.note_id) != note_id or span.doc_char_end > note["char_length"]
                or (span.claim_id is not None and span.claim_id not in claims)
                or (span.result_id is not None and str(span.result_id) not in result_ids)):
            raise InterceptionError("AUDIT_CLAIM_MISMATCH", "An attribution span does not match the note or its "
                                    "claims.", "attribution")
        spans.append({"span_id": str(span.span_id), "doc_char_start": span.doc_char_start,
                      "doc_char_end": span.doc_char_end, "classification": span.classification.value,
                      "claim_id": span.claim_id, "result_id": str(span.result_id) if span.result_id else None})
    return spans


def report_view(report, replay):
    payload = report["payload"]
    by_result = {r["result_id"]: r for r in payload["results"]}
    return {
        "report_id": payload["report_id"],
        "note_id": report["note_id"],
        "patient_pseudo_id": report["patient_pseudo_id"],
        "overall_risk_severity": payload["overall_risk_severity"],
        "pipeline_status": payload["pipeline_status"],
        "counts": payload["counts"],
        "hallucination_events": [
            {"event_id": e["event_id"], "claim_id": e["claim_id"], "category": e["category"],
             "risk_severity": e["risk_severity"], "nli_class": e["nli_class"], "risk_rule": e["risk_rule"],
             "confidence": round(float(e["confidence"]), 3), "matched_fhir_ref": e["matched_fhir_ref"],
             "detected_at": iso(e["detected_at"])}
            for e in report["events"]
        ],
        "attribution_spans": [
            {"span_id": s["span_id"], "note_id": report["note_id"], "doc_char_start": s["doc_char_start"],
             "doc_char_end": s["doc_char_end"], "classification": s["classification"], "claim_id": s["claim_id"],
             "result_id": s["result_id"],
             "explanation": explanation(s["classification"], by_result.get(s["result_id"]))}
            for s in payload["attribution"]
        ],
        "trace_id": report["trace_id"],
        "created_at": iso(report["created_at"]),
        "idempotent_replay": replay,
    }


def _store_call(fn, *args):
    """Store errors are fail-closed: the caller gets 503 AUDIT_WRITE_FAILED and no content."""
    try:
        return fn(*args)
    except PipelineError:
        raise InterceptionError("AUDIT_WRITE_FAILED", "The audit store is unavailable. No content was released.")


def iso(value):
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def count_by(rows, key):
    counts = {}
    for row in rows:
        counts[row[key]] = counts.get(row[key], 0) + 1
    return counts


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()
