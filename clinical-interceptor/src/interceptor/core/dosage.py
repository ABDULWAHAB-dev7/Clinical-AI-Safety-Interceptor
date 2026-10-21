"""Dose limit check (PLAN 4.5, scoped for v1).

Only a claimed dose above the configured maximum single or daily dose is flagged. Comparing a claimed dose with a
documented dose is out of scope: the FHIR subset in DATABASE.md section 1 has no MedicationRequest.
"""

import re
from typing import Optional

from .knowledge import DoseLimit

DOSE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g)\b", re.IGNORECASE)
FREQUENCIES = (
    (re.compile(r"\b(qid|four times (?:a day|daily)|4x daily)\b", re.IGNORECASE), 4),
    (re.compile(r"\b(tid|three times (?:a day|daily)|3x daily)\b", re.IGNORECASE), 3),
    (re.compile(r"\b(bid|twice (?:a day|daily)|two times (?:a day|daily)|2x daily)\b", re.IGNORECASE), 2),
    (re.compile(r"\b(qd|once (?:a day|daily)|daily|every day)\b", re.IGNORECASE), 1),
)
TO_MG = {"mg": 1.0, "g": 1000.0, "mcg": 0.001}


def parse_dose(text: str) -> tuple[Optional[float], Optional[int]]:
    """Returns (single_dose_mg, doses_per_day). Either may be None."""
    match = DOSE.search(text)
    single = float(match.group(1)) * TO_MG[match.group(2).lower()] if match else None
    per_day = next((count for pattern, count in FREQUENCIES if pattern.search(text)), None)
    return single, per_day


def check_dose(text: str, limit: DoseLimit) -> tuple[bool, Optional[float], Optional[str]]:
    """Returns (exceeded, limit_value, unit). limit_value is the limit that was exceeded, or None."""
    single, per_day = parse_dose(text)
    if single is not None and single > limit.max_single_mg:
        return True, limit.max_single_mg, "mg"
    if single is not None and per_day is not None and single * per_day > limit.max_daily_mg:
        return True, limit.max_daily_mg, "mg/day"
    return False, None, None
