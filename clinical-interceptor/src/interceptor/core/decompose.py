"""Stage 2: rule-based, structure-aware decomposition of a clinical note into assertions.

Med-spaCy segmentation and LLM atomic-claim decomposition are not built: their models are not yet approved (architecture
10.1). This version keeps the same contract: each assertion has a type, a negation flag and character offsets.

Structure, handled per line in this order:
  1. A section header (CHIEF COMPLAINT:, SUBJECTIVE:, OBJECTIVE:, HPI:, MEDICATIONS:, ALLERGIES:, ASSESSMENT & PLAN:, ...)
     is a context tag. It is removed from the claim text and kept on each assertion as `section`. The tag lasts until the
     next header.
  2. A list marker ("- ", "* ", "• ", "1. ", "2) ") is removed. Each list item is its own unit. A label right after the
     marker ("- Labs: HbA1c 8.2 %") tags that item only.
  3. Sentences end at . ! ? (a dot inside a number such as 12.1 does not end one).
  4. Inside a sentence, semicolons and coordinate clauses (", and " / ", but ") split it into atomic spans. A split
     needs at least two words after it, so "diabetes and hypertension" is one span.

Every span's offsets come from positions in the original text, never from a rewritten copy, so text[start:end] == text
for every assertion.
"""

import re
from typing import Optional

from .errors import ErrorClass, PipelineError
from .models import Assertion, AssertionType

LINE = re.compile(r"[^\n]+")
HEADER = re.compile(
    r"[ \t]*(?P<name>chief complaint|history of present illness|hpi|past medical history|pmh|social history"
    r"|family history|review of systems|ros|allergies|current medications|home medications|discharge medications"
    r"|medications|meds|labs|lab results|vitals|physical exam|physical examination|subjective|objective|imaging"
    r"|procedures|diagnos[ie]s|problem list|assessment\s*(?:&|and)\s*plan|assessment|plan|hospital course|impression"
    r"|discharge diagnos[ie]s)"
    r"[ \t]*:[ \t]*",
    re.IGNORECASE,
)
LIST_MARK = re.compile(r"[ \t]*(?:[-*•]|\d{1,2}[.)])[ \t]+")
# A dot inside a number ("12.1") does not end a sentence.
SENTENCE = re.compile(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+[.!?]?")
# Semicolons always split. ", and" / ", but" split only when at least two words follow (checked in code).
SPLIT = re.compile(r";|,[ \t]+(?:and|but)[ \t]+", re.IGNORECASE)
NEGATION = re.compile(r"\b(no|not|denies|denied|without|negative for)\b", re.IGNORECASE)
DOSE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|units?)\b", re.IGNORECASE)
# The name may contain digits ("HbA1c"). The value needs a ":" or a space before it, so "HbA1c" is never read as "HbA"+"1".
LAB = re.compile(
    r"^\s*(?P<name>[A-Za-z][A-Za-z0-9 ]{0,30}?)(?::\s*|\s+)(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z%/]+)?",
)
SECTION_ALIASES = {"meds": "medications", "hpi": "history of present illness", "pmh": "past medical history",
                   "ros": "review of systems", "current medications": "medications",
                   "home medications": "medications", "discharge medications": "medications",
                   "lab results": "labs"}


def section_key(name: str) -> str:
    """The context tag for a header: lower case, single spaces, with the usual abbreviations expanded."""
    key = re.sub(r"\s*(?:&|and)\s*", " & ", re.sub(r"\s+", " ", name.strip().lower()))
    if key == "assessment & plan":
        return "plan"  # the plan type is what classification uses
    key = SECTION_ALIASES.get(key, key)
    return key.replace(" ", "_").replace("&", "and")


def decompose(text: str, hints: Optional[dict[str, AssertionType]] = None) -> list[Assertion]:
    """Splits text into assertions. `hints` maps lowercase record terms to a type; it guides typing only."""
    if not isinstance(text, str):
        raise PipelineError(ErrorClass.DECOMPOSITION_SCHEMA_INVALID, "document text must be a string")
    hints = hints or {}

    assertions: list[Assertion] = []
    section: Optional[str] = None
    for line_match in LINE.finditer(text):
        line_start, line_end = line_match.start(), line_match.end()
        position = line_start
        header = HEADER.match(text, position, line_end)
        if header:
            section = section_key(header.group("name"))
            position = header.end()
        marker = LIST_MARK.match(text, position, line_end)
        if marker:
            position = marker.end()
        item_section = section
        if not header:
            label = HEADER.match(text, position, line_end)
            if label:
                item_section = section_key(label.group("name"))
                position = label.end()

        for sentence in SENTENCE.finditer(text, position, line_end):
            for start, end in _clauses(text, sentence.start(), sentence.end()):
                body = text[start:end]
                atype = _classify(item_section, body, body.lower(), hints)
                lab = LAB.match(body) if atype == AssertionType.OBSERVATION_LAB else None
                assertions.append(
                    Assertion(
                        assertion_id=f"a{len(assertions) + 1}",
                        type=atype,
                        span_start=start,
                        span_end=end,
                        text=body,
                        negated=NEGATION.search(body) is not None,
                        name=lab.group("name").strip() if lab else None,
                        value=float(lab.group("value")) if lab else None,
                        unit=lab.group("unit") if lab else None,
                        section=item_section,
                    )
                )
    return assertions


def _clauses(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Atomic (start, end) spans inside one sentence, trimmed of surrounding whitespace. Offsets are absolute."""
    cuts: list[tuple[int, int]] = []  # (cut_start, next_start)
    for split in SPLIT.finditer(text, start, end):
        following = text[split.end():end].split()
        if split.group(0) == ";" and not following:
            continue
        if split.group(0) != ";" and len(following) < 2:
            continue
        cuts.append((split.start(), split.end()))

    spans: list[tuple[int, int]] = []
    piece_start = start
    for cut_start, next_start in cuts:
        spans.append((piece_start, cut_start))
        piece_start = next_start
    spans.append((piece_start, end))

    trimmed: list[tuple[int, int]] = []
    for piece_start, piece_end in spans:
        while piece_start < piece_end and text[piece_start].isspace():
            piece_start += 1
        while piece_end > piece_start and text[piece_end - 1].isspace():
            piece_end -= 1
        if piece_end > piece_start:
            trimmed.append((piece_start, piece_end))
    return trimmed


def _classify(section: Optional[str], body: str, lower: str, hints: dict[str, AssertionType]) -> AssertionType:
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
