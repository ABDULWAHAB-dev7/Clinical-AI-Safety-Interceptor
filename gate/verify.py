"""Classifies each draft span against the patient record.

supported    the record contains the concept with the same polarity
contradicted the record contains the concept with the opposite polarity (hard stop)
ungrounded   no record fact matches the span (flagged, not blocked)

Open question (FR-5.2 is missing): only polarity conflicts are hard stops here.
Unit, date and value checks are not implemented.
"""

import re

from .assertions import NEGATION_CUE, split_spans
from .model import CONTRADICTED, SUPPORTED, UNGROUNDED, Fact, Verdict

NEGATION_WINDOW = 40


def facts_from_record(record):
    facts = [Fact(m.lower(), True, "medications") for m in record.get("medications", [])]
    facts += [Fact(a.lower(), True, "allergies") for a in record.get("allergies", [])]
    facts += [Fact(f["concept"].lower(), bool(f["present"]), "findings") for f in record.get("findings", [])]
    return facts


def verify_draft(text, record):
    facts = facts_from_record(record)
    return [verify_span(span_id, span, facts) for span_id, span in split_spans(text)]


def verify_span(span_id, span, facts):
    lowered = span.lower()
    matches = []
    for fact in facts:
        pos = _find_concept(lowered, fact.concept)
        if pos is not None:
            matches.append((fact, _negated(lowered, pos)))

    if not matches:
        return Verdict(span_id, span, UNGROUNDED, reason="No matching fact in the patient record.")

    # The span asserts the concept is present unless a negation cue precedes it.
    contradicted = [fact for fact, negated in matches if (not negated) != fact.present]
    if contradicted:
        return Verdict(
            span_id,
            span,
            CONTRADICTED,
            evidence=tuple(f.describe() for f in contradicted),
            reason="Draft statement conflicts with the patient record.",
        )
    return Verdict(span_id, span, SUPPORTED, evidence=tuple(f.describe() for f, _ in matches))


def _find_concept(lowered, concept):
    match = re.search(r"\b" + re.escape(concept) + r"\b", lowered)
    return match.start() if match else None


def _negated(lowered, pos):
    window = lowered[max(0, pos - NEGATION_WINDOW):pos]
    return NEGATION_CUE.search(window) is not None
