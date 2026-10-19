"""Entity linking against the local lexicon (PLAN 3.3). No remote terminology service is called.

Longest surface form first, on word boundaries, case-insensitive. A surface form inside a longer token is not linked:
"amoxicillin-clavulanate" does not yield plain amoxicillin. Offsets are code points. Text with no lexicon match gets
no entities, so nothing is invented.
"""

import re
from dataclasses import dataclass

from .contracts import CodeSystem


@dataclass(frozen=True)
class Entity:
    system: CodeSystem
    code: str
    display: str
    surface_text: str
    char_start: int
    char_end: int


def link_entities(text, knowledge, offset=0):
    """Returns entities in document order. `offset` shifts local positions to positions in the full note."""
    taken = []
    found = []
    for entry in knowledge.lexicon:  # already longest surface first
        pattern = re.compile(r"(?<![\w-])" + re.escape(entry.surface) + r"(?![\w-])", re.IGNORECASE)
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < t_end and end > t_start for t_start, t_end in taken):
                continue
            taken.append((start, end))
            found.append(Entity(entry.system, entry.code, entry.display, text[start:end], offset + start, offset + end))
    return sorted(found, key=lambda e: (e.char_start, e.char_end))


def entity_from_model(model):
    """Converts a validated core.contracts.MedicalEntity into an Entity."""
    return Entity(model.system, model.code, model.display, model.surface_text, model.char_start, model.char_end)
