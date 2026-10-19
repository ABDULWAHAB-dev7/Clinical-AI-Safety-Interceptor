"""Turns verification results into a document status.

Journeys (PRD section 4):
  A  all spans supported            -> ready_for_review
  B  any span ungrounded            -> flagged_for_acknowledgment (delivered; spans must be acknowledged)
  C  any span contradicted          -> quarantined (not delivered)
  D  verification fails or errors   -> fail-closed: quarantined_verification_unavailable,
                                       unless a Compliance-approved FailOpenRule matches,
                                       then released_unverified (logged as unverified)

Open question: the latency budget is not given in the PRD, so no timeout is enforced yet.
"""

from dataclasses import asdict, dataclass

from .model import (
    CONTRADICTED,
    FLAGGED,
    QUARANTINE_STATUSES,
    QUARANTINED,
    QUARANTINED_UNAVAILABLE,
    READY,
    RELEASED_UNVERIFIED,
    UNGROUNDED,
)
from .verify import verify_draft

NOTIFY = ["clinician", "llm_integration_owner"]


@dataclass(frozen=True)
class FailOpenRule:
    """A Compliance-approved exception allowing unverified release for one facility and document type."""

    facility: str
    doc_type: str
    approval_ref: str


def status_from_verdicts(verdicts):
    labels = {v.label for v in verdicts}
    if CONTRADICTED in labels:
        return QUARANTINED
    if UNGROUNDED in labels:
        return FLAGGED
    return READY


def run_gate(draft, record, facility, fail_open_rules=(), audit=None):
    doc_id = draft["document_id"]
    doc_type = draft["doc_type"]
    base = {
        "document_id": doc_id,
        "encounter_id": record.get("encounter_id"),
        "facility": facility,
        "doc_type": doc_type,
    }
    try:
        verdicts = verify_draft(draft["text"], record)
    except Exception as exc:  # any verification failure follows the fail-mode (PRD 4.4)
        result = _verification_failed(base, facility, doc_type, fail_open_rules, str(exc))
    else:
        status = status_from_verdicts(verdicts)
        result = {**base, "status": status, "verdicts": [asdict(v) for v in verdicts]}
        if status == QUARANTINED:
            result["notify"] = NOTIFY

    if audit is not None:
        audit.append("gate_result", result)
    return result


def _verification_failed(base, facility, doc_type, fail_open_rules, error):
    rule = next(
        (r for r in fail_open_rules if r.facility == facility and r.doc_type == doc_type),
        None,
    )
    if rule is None:
        return {**base, "status": QUARANTINED_UNAVAILABLE, "verdicts": [], "error": error, "notify": NOTIFY}
    return {
        **base,
        "status": RELEASED_UNVERIFIED,
        "verdicts": [],
        "error": error,
        "fail_open_approval": rule.approval_ref,
    }
