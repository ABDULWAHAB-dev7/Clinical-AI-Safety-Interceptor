"""Individual safety checks. Each check returns a list of Finding objects."""

import re
from dataclasses import dataclass

BLOCK = "block"
REVIEW = "review"

OVERCLAIM = re.compile(
    r"\b(guaranteed|no risk|definitely safe|100% safe|cannot cause harm)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    message: str


def check_patient_id(patient):
    if not str(patient.get("id", "")).strip():
        return [Finding("missing_patient_id", BLOCK, "Patient identifier is missing.")]
    return []


def check_allergies(patient, orders):
    allergies = {str(a).lower() for a in patient.get("allergies", [])}
    return [
        Finding("allergy_conflict", BLOCK, f"{o['drug']} conflicts with a recorded allergy.")
        for o in orders
        if o["drug"].lower() in allergies
    ]


def check_doses(orders, formulary):
    findings = []
    for o in orders:
        entry = formulary.get(o["drug"].lower())
        if entry is None:
            findings.append(
                Finding("unknown_drug", REVIEW, f"{o['drug']} is not in the formulary; dose cannot be verified.")
            )
            continue
        if o["dose_mg"] > entry["max_single_mg"]:
            findings.append(
                Finding(
                    "dose_over_single_max",
                    BLOCK,
                    f"{o['drug']} {o['dose_mg']} mg exceeds the single-dose limit of {entry['max_single_mg']} mg.",
                )
            )
        daily = o["dose_mg"] * o["frequency_per_day"]
        if daily > entry["max_daily_mg"]:
            findings.append(
                Finding(
                    "dose_over_daily_max",
                    BLOCK,
                    f"{o['drug']} totals {daily:g} mg/day, above the daily limit of {entry['max_daily_mg']} mg.",
                )
            )
    return findings


def check_interactions(patient, orders, interactions):
    # Current medications count too: a new order can interact with what the patient already takes.
    drugs = {o["drug"].lower() for o in orders}
    drugs |= {str(m).lower() for m in patient.get("medications", [])}
    return [
        Finding("interaction", REVIEW, f"{a} with {b}: {pair['note']}.")
        for pair in interactions
        for a, b in [pair["drugs"]]
        if a in drugs and b in drugs
    ]


def check_overclaims(text):
    return [
        Finding("overclaim", REVIEW, f"Unqualified safety claim: '{m.group(0)}'.")
        for m in OVERCLAIM.finditer(text)
    ]
