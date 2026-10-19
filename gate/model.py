"""Shared types: verdict labels, document statuses, and record facts."""

from dataclasses import dataclass

SUPPORTED = "supported"
UNGROUNDED = "ungrounded"
CONTRADICTED = "contradicted"

READY = "ready_for_review"
FLAGGED = "flagged_for_acknowledgment"
QUARANTINED = "quarantined"
QUARANTINED_UNAVAILABLE = "quarantined_verification_unavailable"
RELEASED_UNVERIFIED = "released_unverified"
RELEASED_BY_OVERRIDE = "released_by_override"

QUARANTINE_STATUSES = {QUARANTINED, QUARANTINED_UNAVAILABLE}


@dataclass(frozen=True)
class Fact:
    concept: str
    present: bool
    source: str

    def describe(self):
        state = "present" if self.present else "absent"
        return f"{self.source}: {self.concept} ({state})"


@dataclass(frozen=True)
class Verdict:
    span_id: str
    text: str
    label: str
    evidence: tuple = ()
    reason: str = ""
