"""Turns generated text into verifiable units and verifies them (PLAN 5.5).

A unit is one sentence with its claim, or a stretch of text that cannot be verified. Text outside sentences (gaps) may
only be whitespace, sentence punctuation, or a known section header such as "Labs:". Anything else is an unverified
unit, so no unchecked wording can ride along between sentences.
"""

import re
from dataclasses import dataclass
from typing import Optional

from interceptor.core.claims import CLAIM_TEXT_MAX, TRAILING_PUNCTUATION, claim_id_for
from interceptor.core.decompose import decompose
from interceptor.core.entities import link_entities
from interceptor.core.verify import VerificationError, verify_claim

SAFE_GAP = re.compile(
    r"^(?:\s|[.!?;:]|(?:allergies|medications|labs|vitals|assessment|plan|hospital course)\s*:)*$", re.IGNORECASE
)


@dataclass(frozen=True)
class Unit:
    index: int
    start: int
    end: int
    claim: Optional[dict]  # None: nothing to verify, so the unit is UNVERIFIED
    reason: str = ""  # why a unit without a claim could not be verified


@dataclass(frozen=True)
class Verdict:
    severity: str  # SAFE | WARNING | CRITICAL | UNVERIFIED
    result: Optional[dict] = None
    trace: Optional[dict] = None


def analyze(text, note_sha256, knowledge):
    """Sentence units in order, with an UNVERIFIED unit for every stretch of text a sentence does not cover."""
    units, cursor = [], 0

    def add(start, end, claim, reason=""):
        units.append(Unit(len(units), start, end, claim, reason))

    for assertion in decompose(text):
        if assertion.span_start > cursor and not SAFE_GAP.match(text[cursor:assertion.span_start]):
            add(cursor, assertion.span_start, None, "unverified_text")
        claim_text = assertion.text.rstrip(TRAILING_PUNCTUATION).strip()
        if not claim_text or len(claim_text) > CLAIM_TEXT_MAX:
            add(assertion.span_start, assertion.span_end, None, "no_usable_claim")
        else:
            char_end = assertion.span_start + len(claim_text)
            claim = {
                "claim_id": claim_id_for(note_sha256, assertion.span_start, char_end, claim_text),
                "claim_text": claim_text,
                "char_start": assertion.span_start,
                "char_end": char_end,
                "source_sentence": assertion.text,
                "source_sentence_start": assertion.span_start,
                "source_sentence_end": assertion.span_end,
                "entities": link_entities(claim_text, knowledge, offset=assertion.span_start),
            }
            add(assertion.span_start, assertion.span_end, claim)
        cursor = assertion.span_end
    if cursor < len(text) and not SAFE_GAP.match(text[cursor:]):
        add(cursor, len(text), None, "unverified_text")
    return units


def verify_unit(unit, triples, knowledge, verifier_version, nli_model=None):
    """Verifies one unit. Any failure to verify is UNVERIFIED; this function never raises for that."""
    if unit.claim is None:
        return Verdict("UNVERIFIED", trace={"claim_id": None, "branch": unit.reason})
    try:
        result, trace = verify_claim(unit.claim, triples, knowledge, verifier_version, nli_model)
    except VerificationError as exc:
        return Verdict("UNVERIFIED", trace={"claim_id": unit.claim["claim_id"], "branch": "verification_error",
                                            "reason": exc.reason[:80]})
    return Verdict(result["risk_severity"], result, trace)
