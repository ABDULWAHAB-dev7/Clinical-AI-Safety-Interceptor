"""Loads the local knowledge files (PLAN A3: no remote terminology service at runtime).

Any missing or invalid file raises POLICY_LOAD_FAILURE, so the service refuses to start instead of verifying with
partial knowledge.
"""

import csv
import pathlib
from dataclasses import dataclass

import yaml

from interceptor.paths import PROJECT_ROOT

from .contracts import CodeSystem
from .errors import ErrorClass, PipelineError

KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
SYSTEM_KEYS = {"snomed": CodeSystem.SNOMED_CT, "loinc": CodeSystem.LOINC, "rxnorm": CodeSystem.RXNORM}


@dataclass(frozen=True)
class LexiconEntry:
    surface: str
    system: CodeSystem
    code: str
    display: str


@dataclass(frozen=True)
class DoseLimit:
    name: str
    max_single_mg: float
    max_daily_mg: float


@dataclass(frozen=True)
class Thresholds:
    contradiction_min: float
    contradiction_critical_min: float
    entailment_min: float
    value_tolerance_abs: float
    nli_timeout_seconds: float


@dataclass(frozen=True)
class Knowledge:
    lexicon: tuple  # LexiconEntry, longest surface first (ties by surface) so matching is deterministic
    allergy_concept_codes: frozenset
    allergy_classes: dict
    ingredient_classes: dict
    dose_limits: dict
    thresholds: Thresholds


def load_knowledge(directory=KNOWLEDGE_DIR):
    directory = pathlib.Path(directory)
    try:
        return Knowledge(
            lexicon=_load_lexicon(directory / "lexicon_seed.csv"),
            **_load_classes(directory / "drug_class_map.yaml"),
            dose_limits=_load_dose_limits(directory / "dose_limits.yaml"),
            thresholds=_load_thresholds(directory / "thresholds.yaml"),
        )
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError, csv.Error) as exc:
        raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, f"knowledge files: {type(exc).__name__}")


def _load_lexicon(path):
    with path.open(encoding="utf-8", newline="") as fh:
        lines = [line for line in fh if line.strip() and not line.lstrip().startswith("#")]
    entries = []
    for row in csv.DictReader(lines):
        surface = row["surface"].strip().lower()
        code = row["code"].strip()
        if not surface or not code:
            raise ValueError("lexicon row without surface or code")
        entries.append(LexiconEntry(surface, SYSTEM_KEYS[row["system"].strip().lower()], code, row["display"].strip()))
    if not entries:
        raise ValueError("lexicon is empty")
    return tuple(sorted(entries, key=lambda e: (-len(e.surface), e.surface)))


def _load_classes(path):
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        "allergy_concept_codes": frozenset(str(c) for c in raw["allergy_concept_codes"]),
        "allergy_classes": {str(k): str(v) for k, v in raw["allergy_classes"].items()},
        "ingredient_classes": {str(k): str(v) for k, v in raw["ingredient_classes"].items()},
    }


def _load_dose_limits(path):
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["limits"]
    limits = {}
    for code, entry in raw.items():
        limit = DoseLimit(str(entry["name"]), float(entry["max_single_mg"]), float(entry["max_daily_mg"]))
        if limit.max_single_mg <= 0 or limit.max_daily_mg <= 0:
            raise ValueError("dose limits must be positive")
        limits[str(code)] = limit
    return limits


def _load_thresholds(path):
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    thresholds = Thresholds(
        contradiction_min=float(raw["contradiction_min"]),
        contradiction_critical_min=float(raw["contradiction_critical_min"]),
        entailment_min=float(raw["entailment_min"]),
        value_tolerance_abs=float(raw["value_tolerance_abs"]),
        nli_timeout_seconds=float(raw["nli_timeout_seconds"]),
    )
    for value in (thresholds.contradiction_min, thresholds.contradiction_critical_min, thresholds.entailment_min):
        if not 0 < value <= 1:
            raise ValueError("thresholds must be in (0, 1]")
    if thresholds.value_tolerance_abs < 0 or thresholds.nli_timeout_seconds <= 0:
        raise ValueError("tolerance and timeout must be positive")
    return thresholds
