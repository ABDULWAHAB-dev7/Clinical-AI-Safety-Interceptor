"""Clinician acknowledgment before signing, and the documented override path for quarantined documents."""

from .model import QUARANTINE_STATUSES, RELEASED_BY_OVERRIDE, UNGROUNDED

ACTIONS = {"accept", "edit", "reject"}


class ReviewSession:
    def __init__(self, result, audit=None):
        self.result = result
        self.audit = audit
        self.required = {v["span_id"] for v in result["verdicts"] if v["label"] == UNGROUNDED}
        self.acks = {}

    def acknowledge(self, span_id, action, clinician_id):
        if span_id not in self.required:
            raise ValueError(f"{span_id} is not a flagged span")
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {sorted(ACTIONS)}")
        if not clinician_id.strip():
            raise ValueError("clinician_id is required")
        ack = {"span_id": span_id, "action": action, "clinician_id": clinician_id}
        self.acks[span_id] = ack
        if self.audit is not None:
            self.audit.append("acknowledgment", {"document_id": self.result["document_id"], **ack})
        return ack

    def can_sign(self):
        if self.result["status"] in QUARANTINE_STATUSES:
            return False, ["document is quarantined"]
        missing = sorted(self.required - set(self.acks))
        return (not missing), missing


def override(result, reason, approver_id, audit=None):
    if result["status"] not in QUARANTINE_STATUSES:
        raise ValueError("only quarantined documents can be overridden")
    if not reason.strip():
        raise ValueError("override reason is required")
    if not approver_id.strip():
        raise ValueError("approver_id is required")
    released = {
        **result,
        "status": RELEASED_BY_OVERRIDE,
        "override": {"reason": reason, "approver_id": approver_id},
    }
    if audit is not None:
        audit.append("override", {"document_id": result["document_id"], "reason": reason, "approver_id": approver_id})
    return released
