"""Orchestrates the stages with fail-closed handling (architecture 3.1, 7.2) and writes one audit record set per request.

Stage names follow the audit schema (DATABASE.md 3.1): INGEST, RETRIEVE, EXTRACT, VERIFY, ATTRIBUTE, INTERCEPT.
A failed stage marks later stages SKIPPED. INTERCEPT always runs, so every request has a decision.

A document is delivered only if its audit record set was written and the decision is not HARD_BLOCK.
A request whose patient cannot be resolved cannot be recorded (the note row needs a patient pseudo-ID), so it is
never delivered.
"""

import hashlib
import json
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from interceptor.core.attribution import build_spans
from interceptor.core.contracts import NLIClass, RiskSeverity
from interceptor.core.decompose import decompose
from interceptor.core.errors import ErrorClass, PipelineError
from interceptor.core.models import AssertionType, Outcome, Verdict, VerdictRecord
from interceptor.core.policy import decide
from interceptor.core.pseudo import encounter_pseudo_id, patient_pseudo_id
from interceptor.core.severity import ClaimFacts, classify_risk
from interceptor.fhir.bundle import load_bundle
from interceptor.nli.deterministic import verify as deterministic_verify
from interceptor.nli.model import nli_verdict
from interceptor.store.records import EventRecord, RequestRecord, StageRecord

MAX_DOCUMENT_CHARS = 8000  # approximates the 2,000-token ingress cap (architecture 3.2)
PIPELINE_VERSION = "0.2.0"
COMPONENTS = {
    "INGEST": "ingress",
    "RETRIEVE": "fhir-fixture-loader",
    "EXTRACT": "decompose-rules",
    "VERIFY": "verifier",
    "ATTRIBUTE": "attribution",
    "INTERCEPT": "policy",
}

# Category for a verdict on an assertion type (DATABASE.md 3.1 hallucination_category).
CATEGORY = {
    (AssertionType.ALLERGY, Verdict.CONTRADICTED): "DRUG_ALLERGY_CONFLICT",
    (AssertionType.MEDICATION, Verdict.CONTRADICTED): "MEDICATION_DOSE_MISMATCH",
    (AssertionType.OBSERVATION_LAB, Verdict.CONTRADICTED): "LAB_VALUE_MISMATCH",
}
NLI_FOR_VERDICT = {
    Verdict.CONTRADICTED: NLIClass.CONTRADICTION,
    Verdict.UNGROUNDED: NLIClass.NEUTRAL,
    Verdict.SUPPORTED: NLIClass.ENTAILMENT,
}
HIGH_RISK_KIND = {AssertionType.ALLERGY: "allergy", AssertionType.MEDICATION: "medication_dose"}


@dataclass
class VerificationResult:
    request_id: str
    outcome: Outcome
    delivered: bool
    decision: object
    records: list
    spans: list
    errors: list
    pipeline_status: str


def verify_document(text, doc_type, encounter_ref, *, policy, fhir_dir, store, pseudo_key, nli_model=None,
                    source_system="api"):
    request_id = str(uuid.uuid4())
    errors = []
    stages = []
    state = {"failed": None, "bundle": None, "pid": None, "assertions": [], "records": [], "spans": []}

    def run(name, fn, payload):
        """Runs one stage. Records its outcome; on failure, marks the stage and records the error class."""
        if state["failed"] is not None:
            stages.append(stage_row(name, payload(), {}, "SKIPPED", f"skipped after {state['failed']}"))
            return
        started = time.monotonic()
        try:
            output = fn()
        except PipelineError as exc:
            errors.append(exc.error_class.value)
            state["failed"] = name
            stages.append(stage_row(name, payload(), {}, "ERROR", exc.error_class.value, started))
        except Exception:  # any other failure: whole-document hard block (architecture 7.1)
            errors.append(ErrorClass.UNHANDLED_EXCEPTION.value)
            state["failed"] = name
            stages.append(stage_row(name, payload(), {}, "ERROR", ErrorClass.UNHANDLED_EXCEPTION.value, started))
        else:
            stages.append(stage_row(name, payload(), output, "OK", None, started))

    def ingest():
        if not isinstance(text, str):
            raise ValueError("document text must be a string")
        if len(text) > MAX_DOCUMENT_CHARS:
            raise ValueError("document exceeds the ingress size cap")
        return {"char_length": len(text), "doc_type": doc_type}

    def retrieve():
        bundle = load_bundle(encounter_ref, fhir_dir)
        if bundle.parse_failures:
            raise PipelineError(ErrorClass.FHIR_PARSE_FAILURE, ",".join(bundle.parse_failures))
        if not bundle.patient_mrn:
            raise PipelineError(ErrorClass.RETRIEVAL_PARTIAL, "patient identifier missing from bundle")
        state["bundle"] = bundle
        state["pid"] = patient_pseudo_id(bundle.patient_mrn, pseudo_key)
        return {"incomplete_types": sorted(bundle.incomplete_types)}

    def extract():
        assertions = decompose(text, hints=state["bundle"].hints())
        state["assertions"] = assertions
        return {"assertion_count": len(assertions), "types": dict(Counter(a.type.value for a in assertions))}

    def verify():
        records = [_verify_one(a, state["bundle"], policy, nli_model) for a in state["assertions"]]
        state["records"] = records
        return {
            "verdicts": dict(Counter(r.verdict.value for r in records)),
            "verifiers": dict(Counter(r.verifier for r in records)),
            "error_classes": sorted({r.error_class for r in records if r.error_class}),
        }

    def attribute():
        state["spans"] = build_spans(text, state["assertions"], state["records"])
        return {"span_count": len(state["spans"])}

    text_sha = _text_sha(text)
    encounter_sha = _digest({"encounter_ref": encounter_ref})
    run("INGEST", ingest, lambda: {"doc_type": doc_type, "document_sha256": text_sha})
    run("RETRIEVE", retrieve, lambda: {"encounter_ref_sha256": encounter_sha})
    run("EXTRACT", extract, lambda: {"document_sha256": text_sha})
    run("VERIFY", verify, lambda: {"assertions": _positions(state["assertions"])})
    run("ATTRIBUTE", attribute, lambda: {"verdicts": _verdicts(state["records"])})

    assertions, records = state["assertions"], state["records"]
    decision = decide(policy, records, errors, len(assertions))
    stages.append(
        stage_row(
            "INTERCEPT",
            {"errors": errors, "verdicts": _verdicts(records)},
            {"outcome": decision.outcome.value, "rules_fired": decision.rules_fired, "errors": errors},
            "OK",
        )
    )

    pipeline_status = "BLOCKED" if decision.outcome is Outcome.HARD_BLOCK else "VERIFIED"
    audit_ok = False
    if state["pid"] is not None and isinstance(text, str):
        request = RequestRecord(
            request_id=request_id,
            patient_pseudo_id=state["pid"],
            encounter_pseudo_id=encounter_pseudo_id(encounter_ref, pseudo_key),
            source_system=source_system,
            note_type=doc_type,
            note_sha256=text_sha,
            char_length=len(text),
            claim_count=len(assertions),
            pipeline_status=pipeline_status,
            stages=tuple(stages),
            events=tuple(_events(text_sha, assertions, records)),
        )
        try:
            store.record(request)
            audit_ok = True
        except PipelineError:
            errors.append(ErrorClass.AUDIT_WRITE_FAILURE.value)
            decision = decide(policy, records, errors, len(assertions))

    delivered = audit_ok and decision.outcome is not Outcome.HARD_BLOCK
    return VerificationResult(
        request_id=request_id,
        outcome=decision.outcome,
        delivered=delivered,
        decision=decision,
        records=records,
        spans=state["spans"] if delivered else [],
        errors=errors,
        pipeline_status=pipeline_status if audit_ok else "BLOCKED",
    )


def _verify_one(assertion, bundle, policy, nli_model):
    try:
        result = deterministic_verify(assertion, bundle, policy.lab_tolerance_abs)
        if result is not None:
            return VerdictRecord(
                assertion_id=assertion.assertion_id,
                type=assertion.type,
                verdict=result.verdict,
                verifier="deterministic",
                evidence=list(result.evidence),
                confidence=1.0,
            )
        verdict, confidence = nli_verdict(nli_model, bundle.render_premise(), assertion.text, policy)
        return VerdictRecord(
            assertion_id=assertion.assertion_id,
            type=assertion.type,
            verdict=verdict,
            verifier="nli",
            confidence=confidence,
        )
    except PipelineError as exc:
        return VerdictRecord(
            assertion_id=assertion.assertion_id,
            type=assertion.type,
            verdict=Verdict.VERIFICATION_ERROR,
            verifier="none",
            error_class=exc.error_class.value,
        )


def _events(text_sha, assertions, records):
    """One event per claim whose severity is CRITICAL or WARNING. SUPPORTED and SAFE claims are not recorded."""
    by_id = {a.assertion_id: a for a in assertions}
    events = []
    for record in records:
        if record.verdict not in NLI_FOR_VERDICT or record.verdict is Verdict.SUPPORTED:
            continue
        assertion = by_id[record.assertion_id]
        kinds = set()
        if record.verdict is Verdict.CONTRADICTED and assertion.type in HIGH_RISK_KIND:
            kinds.add(HIGH_RISK_KIND[assertion.type])
        confidence = 1.0 if record.confidence is None else record.confidence
        facts = ClaimFacts(
            nli_class=NLI_FOR_VERDICT[record.verdict],
            confidence=confidence,
            is_clinical=True,
            is_medication_claim=assertion.type is AssertionType.MEDICATION,
            matches_allergy=False,  # the RxNorm-to-class lookup needs drug codes the bundle does not carry yet
            entity_kinds=frozenset(kinds),
        )
        severity, rule = classify_risk(facts)
        if severity is RiskSeverity.SAFE:
            continue
        category = CATEGORY.get((assertion.type, record.verdict))
        if category is None:
            category = "UNSUPPORTED_CLAIM" if record.verdict is Verdict.UNGROUNDED else "OTHER"
        ref = None
        if record.evidence:
            first = record.evidence[0]
            ref = f"{first.resource_type}/{first.resource_id}"
        events.append(
            EventRecord(
                claim_id=_claim_id(text_sha, assertion.assertion_id),
                category=category,
                risk_severity=severity.value,
                nli_class=facts.nli_class.value,
                confidence=round(min(max(confidence, 0.0), 1.0), 3),
                claim_text=f"claim:{assertion.type.value}:{'negated' if assertion.negated else 'affirmed'}",
                risk_rule=rule,
                matched_fhir_ref=ref,
                doc_char_start=assertion.span_start,
                doc_char_end=assertion.span_end,
            )
        )
    return events


def stage_row(name, input_payload, output, status, error: Optional[str] = None, started=None):
    latency = None if started is None else int((time.monotonic() - started) * 1000)
    return StageRecord(
        stage=name,
        component_name=COMPONENTS[name],
        component_version=PIPELINE_VERSION,
        input_sha256=_digest(input_payload),
        output_payload=output,
        status=status,
        error_message=error,
        latency_ms=latency,
    )


def _claim_id(text_sha, assertion_id):
    return "clm_" + hashlib.sha256(f"{text_sha}:{assertion_id}".encode()).hexdigest()[:12]


def _text_sha(text):
    if not isinstance(text, str):
        return "invalid"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _positions(assertions):
    return [[a.assertion_id, a.span_start, a.span_end] for a in assertions]


def _verdicts(records):
    return sorted([r.assertion_id, r.verdict.value] for r in records)


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()
