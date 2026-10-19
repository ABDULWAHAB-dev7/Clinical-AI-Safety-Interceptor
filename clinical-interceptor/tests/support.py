"""Shared paths and helpers for the test suite. Run from clinical-interceptor/: python -m unittest discover -s tests -t ."""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "policy" / "default.yaml"
FHIR_DIR = ROOT / "fixtures" / "fhir"
EXAMPLES = ROOT / "examples"


def load_example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
