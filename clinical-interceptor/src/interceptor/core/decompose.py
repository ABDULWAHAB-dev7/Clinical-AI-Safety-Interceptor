"""Stage 2 (MVP stand-in): rule-based segmentation and assertion typing.

The architecture's Med-spaCy segmentation and LLM atomic-claim decomposition are not built: their models are
not yet approved (architecture 10.1). This version keeps the same contract: each assertion has a type, a negation
flag, and character offsets taken from segmentation, not from model output.
"""

import re

from .errors import ErrorClass, PipelineError
from .models import Assertion, AssertionType

LINE = re.compile(r"[^\n]+")
HEADER = re.compile(
    r"^[ \t]*(allergies|medications|labs|vitals|assessment|plan|hospital course)[ \t]*:[ \t]*",
    re.IGNORECASE,
)
# A dot inside a number ("12.1") does not end a sentence.
SENTENCE = re.compile(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+[.!?]?")
NEGATION = re.compile(r"\b(no|not|denies|denied|without|negative for)\b", re.IGNORECASE)
DOSE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?)\b", re.IGNORECASE)
LAB = re.compile(
    r"^\s*(?P<name>[A-Za-z][A-Za-z ]{0,30}?)\s*:?\s*(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z%/]+)?",
)


def decompose(text, hints=None):
    """Splits text into assertions. `hints` maps lowercase record terms to a type; it guides typing only."""
    if not isinstance(text, str):
        raise PipelineError(ErrorClass.DECOMPOSITION_SCHEMA_INVALID, "document text must be a string")
    hints = hints or {}

    assertions = []
    section = None
    for line_match in LINE.finditer(text):
        line = line_match.group(0)
        content_start = line_match.start()
        header = HEADER.match(line)
        if header:
            section = header.group(1).lower()
            content_start += header.end()
            line = line[header.end():]

        for sentence in SENTENCE.finditer(line):
            raw = sentence.group(0)
            body = raw.strip()
            if not body:
                continue
            start = content_start + sentence.start() + (len(raw) - len(raw.lstrip()))
            atype = _classify(section, body, body.lower(), hints)
            lab = LAB.match(body) if atype == AssertionType.OBSERVATION_LAB else None
            assertions.append(
                Assertion(
                    assertion_id=f"a{len(assertions) + 1}",
                    type=atype,
                    span_start=start,
                    span_end=start + len(body),
                    text=body,
                    negated=NEGATION.search(body) is not None,
                    name=lab.group("name").strip() if lab else None,
                    value=float(lab.group("value")) if lab else None,
                    unit=lab.group("unit") if lab else None,
                )
            )
    return assertions


def _classify(section, body, lower, hints):
    if section == "allergies" or "allerg" in lower:
        return AssertionType.ALLERGY
    if section == "labs":
        return AssertionType.OBSERVATION_LAB
    if section == "vitals":
        return AssertionType.OBSERVATION_VITAL
    for term, atype in hints.items():
        if re.search(r"\b" + re.escape(term) + r"\b", lower):
            return atype
    if section == "medications" or DOSE.search(body) or "prescri" in lower:
        return AssertionType.MEDICATION
    if section == "plan" or lower.startswith("plan"):
        return AssertionType.PLAN
    if NEGATION.search(body):
        return AssertionType.NEGATED_FINDING
    return AssertionType.OTHER_CLINICAL
