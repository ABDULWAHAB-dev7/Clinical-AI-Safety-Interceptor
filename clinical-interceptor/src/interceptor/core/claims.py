"""Claim-level mapping for the API (API.md 5.1.4 and 6.1).

decompose_claims: assertions -> AtomicProposition dicts with spec claim IDs.
overall_severity and summary: roll up VerificationResult dicts. Verification itself is in core.verify.
"""

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, Optional
from uuid import UUID

from interceptor.core.contracts import RiskSeverity
from interceptor.core.decompose import decompose
from interceptor.core.entities import link_entities
from interceptor.core.knowledge import Knowledge

SEVERITY_ORDER = {RiskSeverity.CRITICAL: 2, RiskSeverity.WARNING: 1, RiskSeverity.SAFE: 0}
CLAIM_TEXT_MAX = 512
TRAILING_PUNCTUATION = ".!?;:"


def claim_id_for(note_sha256: str, char_start: int, char_end: int, claim_text: str) -> str:
    """API.md 5.1.4: "clm_" + first 12 hex chars of SHA-256(note_sha256:char_start:char_end:claim_text)."""
    digest = hashlib.sha256(f"{note_sha256}:{char_start}:{char_end}:{claim_text}".encode()).hexdigest()
    return "clm_" + digest[:12]


def decompose_claims(text: str, note_sha256: str, note_id: UUID | str, knowledge: Optional[Knowledge] = None,
                     include_entities: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (claims, warnings). A sentence whose claim text is empty or too long is skipped with a warning.

    With `knowledge`, each claim carries the entities found by the local lexicon (offsets relative to the full note).
    Without a lexicon match a claim keeps an empty entity list; nothing is invented (PLAN A3).
    """
    claims: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for assertion in decompose(text):
        claim_text = assertion.text.rstrip(TRAILING_PUNCTUATION).strip()
        if not claim_text or len(claim_text) > CLAIM_TEXT_MAX:
            warnings.append({
                "code": "INTERCEPT_CLAIM_SKIPPED",
                "message": "A sentence produced no usable claim text and was skipped.",
                "param": None,
            })
            continue
        char_start = assertion.span_start
        char_end = char_start + len(claim_text)
        claims.append({
            "claim_id": claim_id_for(note_sha256, char_start, char_end, claim_text),
            "note_id": str(note_id),
            "source_sentence": assertion.text,
            "source_sentence_start": assertion.span_start,
            "source_sentence_end": assertion.span_end,
            "claim_text": claim_text,
            "char_start": char_start,
            "char_end": char_end,
            "entities": _entities(claim_text, char_start, knowledge) if include_entities else [],
        })
    return claims, warnings


def _entities(claim_text: str, char_start: int, knowledge: Optional[Knowledge]) -> list[dict[str, Any]]:
    if knowledge is None:
        return []
    return [
        {"system": e.system.value, "code": e.code, "display": e.display, "surface_text": e.surface_text,
         "char_start": e.char_start, "char_end": e.char_end}
        for e in link_entities(claim_text, knowledge, offset=char_start)
    ]


def overall_severity(results: Sequence[Mapping[str, Any]]) -> str:
    if not results:
        return RiskSeverity.SAFE.value
    return max((RiskSeverity(r["risk_severity"]) for r in results), key=SEVERITY_ORDER.__getitem__).value


def summary(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {s.value: 0 for s in RiskSeverity}
    for r in results:
        counts[r["risk_severity"]] += 1
    return {"total": len(results), "by_severity": counts}
