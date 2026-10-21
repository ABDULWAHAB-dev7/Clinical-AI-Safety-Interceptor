"""Policy loading and the decision engine (architecture 3.7, 7.2).

Hard-stop rules are defined here, in code, and cannot be disabled by configuration (architecture section 5).
The policy file may only tune thresholds and scope; its key set is fixed, so unknown keys are refused.
"""

import pathlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import yaml

from .errors import ErrorClass, PipelineError
from .models import Decision, Outcome, Verdict, VerdictRecord

REQUIRED_KEYS = {"policy_version", "doc_types_in_scope", "nli", "tolerances"}

R1 = "R1_pipeline_or_audit_failure"
R2 = "R2_verification_error"
R3 = "R3_contradiction"
R4 = "R4_no_assertions"
C1 = "C1_ungrounded_flag"
HARD_STOP_RULES = (R1, R2, R3, R4)
CONFIGURABLE_RULES = (C1,)


@dataclass(frozen=True)
class Policy:
    version: str
    doc_types_in_scope: frozenset[str]
    entailment_threshold: float
    contradiction_threshold: float
    lab_tolerance_abs: float


def load_policy(path: str | pathlib.Path) -> Policy:
    """Loads a versioned policy file. Any problem raises POLICY_LOAD_FAILURE; the service must not start."""
    try:
        raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, type(exc).__name__)

    if not isinstance(raw, dict) or set(raw) != REQUIRED_KEYS:
        raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, f"policy keys must be exactly {sorted(REQUIRED_KEYS)}")
    try:
        policy = Policy(
            version=str(raw["policy_version"]),
            doc_types_in_scope=frozenset(str(t) for t in raw["doc_types_in_scope"]),
            entailment_threshold=float(raw["nli"]["entailment_threshold"]),
            contradiction_threshold=float(raw["nli"]["contradiction_threshold"]),
            lab_tolerance_abs=float(raw["tolerances"]["observation_lab_abs"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, f"invalid policy: {type(exc).__name__}")

    if not policy.version or not policy.doc_types_in_scope:
        raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "version and document scope are required")
    for threshold in (policy.entailment_threshold, policy.contradiction_threshold):
        if not 0 < threshold <= 1:
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "NLI thresholds must be in (0, 1]")
    if policy.lab_tolerance_abs < 0:
        raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "lab tolerance cannot be negative")
    return policy


def decide(policy: Policy, records: Iterable[VerdictRecord], errors: Sequence[object],
           assertion_count: int) -> Decision:
    """Evaluates rules in fixed order. Any hard-stop rule forces HARD_BLOCK; C1 alone gives WARN."""
    verdicts = {r.verdict for r in records}
    fired = []
    if errors:
        fired.append(R1)
    if Verdict.VERIFICATION_ERROR in verdicts:
        fired.append(R2)
    if Verdict.CONTRADICTED in verdicts:
        fired.append(R3)
    if assertion_count == 0:
        fired.append(R4)
    if Verdict.UNGROUNDED in verdicts:
        fired.append(C1)

    if any(rule in HARD_STOP_RULES for rule in fired):
        outcome = Outcome.HARD_BLOCK
    elif C1 in fired:
        outcome = Outcome.WARN
    else:
        outcome = Outcome.PASS_THROUGH
    return Decision(
        outcome=outcome,
        policy_version=policy.version,
        rules_evaluated=list(HARD_STOP_RULES + CONFIGURABLE_RULES),
        rules_fired=fired,
    )
