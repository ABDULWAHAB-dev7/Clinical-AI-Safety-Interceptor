"""The document check behind POST /v1/verify (the native endpoint of ARCHITECTURE 6.2, FR-6.3).

It is a single call that runs the same engine as the three /v1/interceptor endpoints:

  1. the encounter's FHIR bundle is read from the fixture directory, and its patient gives the pseudonym;
  2. engine.decompose records the note and its claims;
  3. engine.verify classifies each claim against that bundle;
  4. engine.audit writes the report, its events and its attribution spans.

So /v1/verify and the /v1/interceptor endpoints give the same verdict and write the same audit rows for the same note.
The response keeps the legacy top-level fields, so existing callers still parse it.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from interceptor.core.contracts import MedicalEntity, RiskSeverity, VerificationResult
from interceptor.core.models import Outcome
from interceptor.core.pseudo import encounter_pseudo_id, patient_pseudo_id
from interceptor.fhir.bundle import ENCOUNTER_RE, MRN_SYSTEM

from . import engine
from .context import load_patient_context
from .errors import InterceptionError

ENCOUNTER_BUNDLE_CODE = "INTERCEPT_BUNDLE_INVALID"  # an existing code: the contract does not change (Gate G3)


def _encounter_bundle(fhir_dir, encounter_ref):
    if not ENCOUNTER_RE.match(encounter_ref):
        raise InterceptionError(ENCOUNTER_BUNDLE_CODE, "The encounter reference is not valid.", "encounter_ref")
    path = Path(fhir_dir) / f"{encounter_ref}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InterceptionError(ENCOUNTER_BUNDLE_CODE, "The encounter bundle is unavailable. No content was released.",
                                "encounter_ref")
    if not isinstance(raw, dict) or raw.get("resourceType") != "Bundle":
        raise InterceptionError(ENCOUNTER_BUNDLE_CODE, "The encounter bundle is not a FHIR Bundle.", "encounter_ref")
    return raw


def _patient_mrn(raw):
    for entry in raw.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(resource, dict) and resource.get("resourceType") == "Patient":
            for identifier in resource.get("identifier", []):
                if identifier.get("system") == MRN_SYSTEM and identifier.get("value"):
                    return str(identifier["value"])
    raise InterceptionError(ENCOUNTER_BUNDLE_CODE, "The encounter bundle has no patient identifier.", "encounter_ref")


def check_document(state, *, document_text, doc_type, encounter_ref, source_system, policy):
    """Runs the whole check and returns the legacy response body."""
    raw = _encounter_bundle(state.fhir_dir, encounter_ref)
    mrn = _patient_mrn(raw)
    patient = patient_pseudo_id(mrn, state.pseudo_key)
    encounter = encounter_pseudo_id(encounter_ref, state.pseudo_key)
    # Validate the record before anything is written, so a rejected bundle leaves no note behind.
    load_patient_context(raw, patient, state)

    registration, decomposed = engine.decompose(
        state,
        patient_pid=patient,
        encounter_pid=encounter,
        text=document_text,
        source_system=source_system,
        note_type=doc_type,
        include_entities=True,
        max_claims=500,
    )
    claims = [
        SimpleNamespace(
            claim_id=c["claim_id"],
            claim_text=c["claim_text"],
            char_start=c["char_start"],
            char_end=c["char_end"],
            note_id=registration.note_id,
            entities=[MedicalEntity.model_validate(e) for e in c["entities"]],
        )
        for c in decomposed["claims"]
    ]

    verified = engine.verify(
        state,
        patient_pid=patient,
        raw_bundle=raw,
        claims=claims,
        note_id=registration.note_id,
    )
    results = [VerificationResult.model_validate(r) for r in verified["results"]]
    scope = engine.digest({"document_check": registration.note_id})
    report = engine.audit(
        state,
        patient_pid=patient,
        note_id=registration.note_id,
        verify_trace_ids=[verified["verify_trace_id"]],
        claims=claims,
        results=results,
        attribution=None,
        create_for={"CRITICAL", "WARNING"},
        body_sha=engine.digest({"note_id": registration.note_id, "doc_type": doc_type}),
        scope=scope,
    )
    return _legacy_body(report, [r.model_dump(mode="json") for r in results], policy)


def _legacy_body(report, verdicts, policy):
    """The fields /v1/verify has always returned, derived from the report."""
    blocked = report["pipeline_status"] == "BLOCKED"
    warned = report["counts"].get(RiskSeverity.WARNING.value, 0) > 0
    if blocked:
        outcome = Outcome.HARD_BLOCK
    elif warned:
        outcome = Outcome.WARN
    else:
        outcome = Outcome.PASS_THROUGH
    flagged = [v for v in verdicts if v["risk_severity"] != RiskSeverity.SAFE.value]
    delivered = not blocked
    body = {
        "request_id": report["report_id"],
        "note_id": report["note_id"],
        "trace_id": report["trace_id"],
        "outcome": outcome.value,
        "delivered": delivered,
        "pipeline_status": report["pipeline_status"],
        "policy_version": policy.version,
        "rules_fired": sorted({v["risk_rule"] for v in flagged if v.get("risk_rule")}
                              | set(report.get("hard_stop_rules", []))),
        "errors": [],
        "verdicts": verdicts,
    }
    if delivered:  # blocked documents return no text and no spans (architecture 6.2)
        body["spans"] = report["attribution_spans"]
    return body
