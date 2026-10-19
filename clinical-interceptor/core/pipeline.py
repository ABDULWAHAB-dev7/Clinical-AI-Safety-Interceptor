"""Orchestrates Stages 1-6 with fail-closed handling (architecture sections 3.1 and 7.2).

Every failure resolves to a hard block. A document is delivered only if the audit record was persisted and the
decision is not HARD_BLOCK.
"""

import hashlib
import uuid
from dataclasses import dataclass

from fhir.bundle import load_bundle
from nli.deterministic import verify as deterministic_verify
from nli.model import nli_verdict
from store.audit import AuditLog

from .attribution import build_spans
from .decompose import decompose
from .errors import ErrorClass, PipelineError
from .models import Outcome, Verdict, VerdictRecord
from .policy import decide

MAX_DOCUMENT_CHARS = 8000  # approximates the 2,000-token ingress cap (architecture 3.2)


@dataclass
class VerificationResult:
    request_id: str
    outcome: Outcome
    delivered: bool
    decision: object
    records: list
    spans: list
    errors: list


def verify_document(text, doc_type, encounter_ref, *, policy, fhir_dir, audit, nli_model=None):
    request_id = str(uuid.uuid4())
    errors = []
    records, spans, assertions = [], [], []
    try:
        bundle = load_bundle(encounter_ref, fhir_dir)
        if bundle.parse_failures:
            errors.append(ErrorClass.FHIR_PARSE_FAILURE.value)
        assertions = decompose(text, hints=bundle.hints())
        records = [_verify_one(a, bundle, policy, nli_model) for a in assertions]
        spans = build_spans(text, assertions, records)
    except PipelineError as exc:
        errors.append(exc.error_class.value)
        records, spans = [], []
    except Exception:  # any other failure is a whole-document hard block (architecture 7.1)
        errors.append(ErrorClass.UNHANDLED_EXCEPTION.value)
        records, spans = [], []

    decision = decide(policy, records, errors, len(assertions))
    audit_ok = True
    try:
        audit.write_records(request_id, _audit_events(text, doc_type, encounter_ref, policy, records, decision, errors))
    except PipelineError:
        audit_ok = False
        errors.append(ErrorClass.AUDIT_WRITE_FAILURE.value)
        decision = decide(policy, records, errors, len(assertions))

    delivered = audit_ok and decision.outcome != Outcome.HARD_BLOCK
    return VerificationResult(
        request_id=request_id,
        outcome=decision.outcome,
        delivered=delivered,
        decision=decision,
        records=records,
        spans=spans if delivered else [],
        errors=errors,
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
            )
        verdict = nli_verdict(nli_model, bundle.render_premise(), assertion.text, policy)
        return VerdictRecord(assertion_id=assertion.assertion_id, type=assertion.type, verdict=verdict, verifier="nli")
    except PipelineError as exc:
        return VerdictRecord(
            assertion_id=assertion.assertion_id,
            type=assertion.type,
            verdict=Verdict.VERIFICATION_ERROR,
            verifier="none",
            error_class=exc.error_class.value,
        )


def _audit_events(text, doc_type, encounter_ref, policy, records, decision, errors):
    events = [
        {
            "kind": "request",
            "document_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if isinstance(text, str) else None,
            "doc_type": doc_type,
            "encounter_ref_sha256": hashlib.sha256(encounter_ref.encode("utf-8")).hexdigest(),
            "policy_version": policy.version,
        }
    ]
    events += [{"kind": "assertion", **r.model_dump(mode="json")} for r in records]
    events.append(
        {
            "kind": "decision",
            "outcome": decision.outcome.value,
            "rules_evaluated": decision.rules_evaluated,
            "rules_fired": decision.rules_fired,
            "errors": errors,
        }
    )
    return events
