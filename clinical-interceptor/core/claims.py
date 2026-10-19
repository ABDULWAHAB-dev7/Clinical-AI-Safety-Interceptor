"""Claim-level mapping for the API (API.md 5.1.4 and 6.1).

decompose_claims: assertions -> AtomicProposition dicts with spec claim IDs.
verify_claims:    claim texts + bundle -> VerificationResult dicts with NLI class and severity.

Deterministic verdicts only. There is no NLI model yet, so a claim without a coded match raises
VerificationError, and the API returns INTERCEPT_VERIFIER_UNAVAILABLE rather than guessing.
"""

import hashlib
import uuid

from core.contracts import NLIClass, RiskSeverity
from core.decompose import decompose
from core.models import AssertionType, Verdict
from core.severity import ClaimFacts, classify_risk
from nli.deterministic import verify as deterministic_verify

SEVERITY_ORDER = {RiskSeverity.CRITICAL: 2, RiskSeverity.WARNING: 1, RiskSeverity.SAFE: 0}
CLAIM_TEXT_MAX = 512
TRAILING_PUNCTUATION = ".!?;:"


class VerificationError(Exception):
    """A claim cannot be verified (no coded match and no NLI model). Fail closed."""


def claim_id_for(note_sha256, char_start, char_end, claim_text):
    """API.md 5.1.4: "clm_" + first 12 hex chars of SHA-256(note_sha256:char_start:char_end:claim_text)."""
    digest = hashlib.sha256(f"{note_sha256}:{char_start}:{char_end}:{claim_text}".encode("utf-8")).hexdigest()
    return "clm_" + digest[:12]


def decompose_claims(text, note_sha256, note_id):
    """Returns (claims, warnings). A sentence whose claim text is empty or too long is skipped with a warning."""
    claims, warnings = [], []
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
            "entities": [],  # entity extraction is not built yet; DATABASE.md 2.2 entities are not emitted
        })
    return claims, warnings


def verify_claims(claims, bundle, policy, verifier_version):
    """claims: list of dicts with claim_id and claim_text. Returns VerificationResult dicts."""
    results = []
    for claim in claims:
        assertion = _first_assertion(claim["claim_text"], bundle)
        verdict_result = deterministic_verify(assertion, bundle, policy.lab_tolerance_abs)
        if verdict_result is None:
            raise VerificationError(claim["claim_id"])
        if verdict_result.verdict is Verdict.SUPPORTED:
            nli_class = NLIClass.ENTAILMENT
        elif verdict_result.verdict is Verdict.CONTRADICTED:
            nli_class = NLIClass.CONTRADICTION
        else:
            raise VerificationError(claim["claim_id"])
        facts = ClaimFacts(
            nli_class=nli_class,
            confidence=1.0,
            is_clinical=True,
            is_medication_claim=assertion.type is AssertionType.MEDICATION,
            matches_allergy=False,  # rule 1 needs an RxNorm class lookup; not built yet
            entity_kinds=frozenset(_kinds(assertion.type, nli_class)),
        )
        severity, rule = classify_risk(facts)
        ref = verdict_result.evidence[0] if verdict_result.evidence else None
        results.append({
            "result_id": str(uuid.uuid4()),
            "claim_id": claim["claim_id"],
            "matched_fhir_ref": f"{ref.resource_type}/{ref.resource_id}" if ref else None,
            "nli_class": nli_class.value,
            "confidence": facts.confidence,
            "risk_severity": severity.value,
            "risk_rule": rule,
            "verifier_version": verifier_version,
        })
    return results


def overall_severity(results):
    if not results:
        return RiskSeverity.SAFE.value
    return max((RiskSeverity(r["risk_severity"]) for r in results), key=SEVERITY_ORDER.__getitem__).value


def summary(results):
    counts = {s.value: 0 for s in RiskSeverity}
    for r in results:
        counts[r["risk_severity"]] += 1
    return {"total": len(results), "by_severity": counts}


def _first_assertion(claim_text, bundle):
    found = decompose(claim_text, hints=bundle.hints())
    if not found:
        raise VerificationError("empty claim")
    return found[0]


def _kinds(assertion_type, nli_class):
    if nli_class is NLIClass.CONTRADICTION and assertion_type is AssertionType.ALLERGY:
        return {"allergy"}
    if nli_class is NLIClass.CONTRADICTION and assertion_type is AssertionType.MEDICATION:
        return {"medication_dose"}
    return set()
