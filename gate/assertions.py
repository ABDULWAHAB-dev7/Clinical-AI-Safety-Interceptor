"""Splits a draft into spans (one per sentence) and detects negation cues.

Open question (PRD section 5.2 is missing): the PRD asks for decomposition into
atomic assertions. Sentence splitting is a placeholder for that.
"""

import re

SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
NEGATION_CUE = re.compile(
    r"\b(no|not|denies|denied|without|absent|negative for|ruled out)\b",
    re.IGNORECASE,
)


def split_spans(text):
    spans = []
    for piece in SPLIT.split(text):
        piece = piece.strip()
        if piece:
            spans.append((f"s{len(spans) + 1}", piece))
    return spans
