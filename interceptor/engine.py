"""Runs every rule over a proposed AI output and produces one decision.

Decisions: "allow" (no findings), "review" (a human should look), "block" (do not show).
"""

import json
import numbers
import pathlib
from dataclasses import asdict

from . import rules

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"


class InvalidRequest(ValueError):
    pass


def load_reference(data_dir=DATA_DIR):
    formulary = json.loads((data_dir / "formulary.sample.json").read_text(encoding="utf-8"))
    interactions = json.loads((data_dir / "interactions.sample.json").read_text(encoding="utf-8"))
    return formulary, interactions


def validate(request):
    if not isinstance(request, dict):
        raise InvalidRequest("request must be a JSON object")
    patient = request.get("patient")
    output = request.get("output")
    if not isinstance(patient, dict):
        raise InvalidRequest("patient must be an object")
    if not isinstance(output, dict):
        raise InvalidRequest("output must be an object")

    text = output.get("text", "")
    if not isinstance(text, str):
        raise InvalidRequest("output.text must be a string")

    orders = output.get("orders", [])
    if not isinstance(orders, list):
        raise InvalidRequest("output.orders must be a list")
    for order in orders:
        if not isinstance(order, dict) or not isinstance(order.get("drug"), str) or not order["drug"].strip():
            raise InvalidRequest("each order needs a non-empty drug name")
        if not _positive_number(order.get("dose_mg")):
            raise InvalidRequest("each order needs a positive dose_mg")
        if not _positive_number(order.get("frequency_per_day")):
            raise InvalidRequest("each order needs a positive frequency_per_day")
    return patient, text, orders


def _positive_number(value):
    return isinstance(value, numbers.Real) and not isinstance(value, bool) and value > 0


def decide(findings):
    if any(f.severity == rules.BLOCK for f in findings):
        return "block"
    if findings:
        return "review"
    return "allow"


def intercept(request, formulary, interactions):
    patient, text, orders = validate(request)
    findings = [
        *rules.check_patient_id(patient),
        *rules.check_allergies(patient, orders),
        *rules.check_doses(orders, formulary),
        *rules.check_interactions(patient, orders, interactions),
        *rules.check_overclaims(text),
    ]
    return {"decision": decide(findings), "findings": [asdict(f) for f in findings]}
