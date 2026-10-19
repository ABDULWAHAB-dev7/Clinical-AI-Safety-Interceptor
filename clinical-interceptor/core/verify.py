"""Verifier service (PLAN 4.3 to 4.6): claims + ground-truth triples -> VerificationResult.

Classification branches, in order. The first that applies sets the NLI class:
  medication claim       no medication facts exist in the FHIR subset, so NEUTRAL; or CONTRADICTION if a claimed dose
                         exceeds the configured limit (rule 2)
  allergy-concept claim  denial of allergies against a documented allergy is CONTRADICTION; otherwise ENTAILMENT
  coded condition/lab    same code as a triple: condition present or negated; lab value within tolerance or not
  no candidates          NEUTRAL (nothing in the record bears on the claim)
  anything else          needs the NLI scorer; without one the claim fails closed (VerificationError)

The severity policy then sets risk_severity and risk_rule. The NLI class is never changed by a rule.
Every result is validated against core.contracts.VerificationResult; an inconsistent result fails closed.
"""

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Optional

from fhir.triples import verbalize
from pydantic import ValidationError

from .contracts import CodeSystem, NLIClass, VerificationResult
from .decompose import NEGATION
from .dosage import check_dose
from .entities import link_entities
from .retrieval import retrieve
from .severity import ClaimFacts, classify_risk

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class VerificationError(Exception):
    """A claim cannot be verified. The caller must fail closed and never default a severity."""

    def __init__(self, claim_id, reason):
        super().__init__(f"{claim_id}: {reason}")
        self.claim_id = claim_id
        self.reason = reason


@dataclass(frozen=True)
class Decision:
    nli_class: NLIClass
    confidence: float
    ref: Optional[str]
    branch: str
    kinds: frozenset = frozenset()
    is_medication: bool = False
    matches_allergy: bool = False


@dataclass
class VerifyOutcome:
    results: list = field(default_factory=list)
    traces: list = field(default_factory=list)


def verify_claims(claims, triples, knowledge, verifier_version, nli_model=None):
    outcome = VerifyOutcome()
    for claim in claims:
        result, trace = verify_claim(claim, triples, knowledge, verifier_version, nli_model)
        outcome.results.append(result)
        outcome.traces.append(trace)
    return outcome


def verify_claim(claim, triples, knowledge, verifier_version, nli_model=None):
    """claim: dict with claim_id, claim_text, and optional entities (list of core.entities.Entity)."""
    claim_id, text = claim["claim_id"], claim["claim_text"]
    entities = list(claim.get("entities") or link_entities(text, knowledge, offset=claim.get("char_start", 0)))
    candidates = retrieve(text, entities, triples, knowledge)
    trace = {
        "claim_id": claim_id,
        "candidates": [{"source_ref": c.triple.source_ref, "score": c.score, "reasons": list(c.reasons)}
                       for c in candidates],
        "drug_class_unknown": False,
        "branch": None,
    }

    allergy_triples = sorted((t for t in triples if t.predicate == "HAS_ALLERGY"), key=lambda t: t.source_ref)
    rx = [e for e in entities if e.system is CodeSystem.RXNORM]
    allergy_entities = [e for e in entities if e.system is CodeSystem.SNOMED_CT
                        and e.code in knowledge.allergy_concept_codes]
    coded = [e for e in entities if e not in rx and e not in allergy_entities]
    negated = NEGATION.search(text) is not None

    if rx:
        decision = _medication(text, rx, allergy_triples, knowledge, trace)
    elif allergy_entities:
        decision = _allergy_concept(allergy_triples, negated)
    else:
        decision = _coded_match(text, coded, triples, knowledge, negated)
        if decision is None:
            decision = Decision(NLIClass.NEUTRAL, 1.0, None, "no_candidates") if not candidates else _nli(
                claim_id, text, candidates, knowledge, nli_model)
    trace["branch"] = decision.branch

    facts = ClaimFacts(
        nli_class=decision.nli_class,
        confidence=decision.confidence,
        is_clinical=True,
        is_medication_claim=decision.is_medication,
        matches_allergy=decision.matches_allergy,
        entity_kinds=decision.kinds,
    )
    severity, rule = classify_risk(facts, entailment_floor=knowledge.thresholds.entailment_min)
    try:
        result = VerificationResult(
            result_id=uuid.uuid4(),
            claim_id=claim_id,
            matched_fhir_ref=decision.ref,
            nli_class=decision.nli_class,
            confidence=min(max(decision.confidence, 0.0), 1.0),
            risk_severity=severity,
            risk_rule=rule,
            verifier_version=verifier_version,
        )
    except ValidationError:
        raise VerificationError(claim_id, "result is inconsistent with the VerificationResult contract")
    return result.model_dump(mode="json"), trace


def _medication(text, rx, allergy_triples, knowledge, trace):
    classes = {knowledge.ingredient_classes.get(e.code) for e in rx} - {None}
    if not classes:
        trace["drug_class_unknown"] = True  # a known gap: no class, so rule 1 cannot fire (PLAN 4.5)
    allergy = next((t for t in allergy_triples if t.drug_class and t.drug_class in classes), None)
    exceeded = any(
        e.code in knowledge.dose_limits and check_dose(text, knowledge.dose_limits[e.code])[0] for e in rx
    )
    return Decision(
        nli_class=NLIClass.CONTRADICTION if exceeded else NLIClass.NEUTRAL,
        confidence=1.0,
        ref=allergy.source_ref if allergy else None,
        branch="dose_limit_exceeded" if exceeded else "no_medication_facts",
        kinds=frozenset({"medication_dose"}) if exceeded else frozenset(),
        is_medication=True,
        matches_allergy=allergy is not None,
    )


def _allergy_concept(allergy_triples, negated):
    if not allergy_triples:
        return Decision(NLIClass.NEUTRAL, 1.0, None, "no_allergy_record")  # absence is not evidence of no allergy
    ref = allergy_triples[0].source_ref
    if negated:
        return Decision(NLIClass.CONTRADICTION, 1.0, ref, "allergy_denied_but_documented", frozenset({"allergy"}))
    return Decision(NLIClass.ENTAILMENT, 1.0, ref, "allergy_documented")


def _coded_match(text, coded, triples, knowledge, negated):
    for entity in sorted(coded, key=lambda e: e.char_start):
        matches = sorted((t for t in triples if t.object_code == entity.code and t.object_system is entity.system),
                         key=lambda t: t.source_ref)
        if not matches:
            continue
        triple = matches[0]
        if triple.predicate == "HAS_CONDITION":
            if negated:
                kinds = frozenset({"active_diagnosis"}) if triple.status == "active" else frozenset()
                return Decision(NLIClass.CONTRADICTION, 1.0, triple.source_ref, "condition_denied_but_documented", kinds)
            return Decision(NLIClass.ENTAILMENT, 1.0, triple.source_ref, "condition_documented")
        if triple.predicate == "HAS_OBSERVATION":
            claimed = _claimed_number(text, entity)
            if claimed is None or triple.numeric_value is None:
                continue  # no value to compare: leave it to the NLI scorer
            if abs(claimed - triple.numeric_value) <= knowledge.thresholds.value_tolerance_abs:
                return Decision(NLIClass.ENTAILMENT, 1.0, triple.source_ref, "observation_value_match")
            return Decision(NLIClass.CONTRADICTION, 1.0, triple.source_ref, "observation_value_mismatch")
    return None


def _claimed_number(text, entity):
    """First number after the entity in the claim, ignoring ISO dates."""
    blanked = DATE.sub(lambda m: " " * len(m.group()), text)
    start = max(blanked.lower().find(entity.surface_text.lower()), 0)
    match = NUMBER.search(blanked, start + len(entity.surface_text))
    return float(match.group()) if match else None


def _nli(claim_id, text, candidates, knowledge, model):
    if model is None:
        raise VerificationError(claim_id, "unresolved candidates need the NLI scorer, which is not available")
    thresholds = knowledge.thresholds
    contradiction = None
    best = None
    for candidate in candidates:
        scores = _score(claim_id, model, verbalize(candidate.triple), text, thresholds.nli_timeout_seconds)
        entailment, neutral, contra = scores["entailment"], scores["neutral"], scores["contradiction"]
        if contra >= thresholds.contradiction_min and (contradiction is None or contra > contradiction[0]):
            contradiction = (contra, candidate)  # contradiction takes precedence over entailment
        if best is None or entailment > best[0]:
            best = (entailment, neutral, contra, candidate)
    if contradiction is not None:
        contra, candidate = contradiction
        kinds = {"allergy"} if candidate.triple.predicate == "HAS_ALLERGY" else (
            {"active_diagnosis"} if candidate.triple.predicate == "HAS_CONDITION"
            and candidate.triple.status == "active" else set())
        return Decision(NLIClass.CONTRADICTION, contra, candidate.triple.source_ref, "nli_contradiction",
                        frozenset(kinds))
    entailment, neutral, contra, candidate = best
    if entailment >= max(neutral, contra):
        return Decision(NLIClass.ENTAILMENT, entailment, candidate.triple.source_ref, "nli_entailment")
    return Decision(NLIClass.NEUTRAL, max(neutral, contra), None, "nli_neutral")


def _score(claim_id, model, premise, hypothesis, timeout):
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        scores = executor.submit(model.score, premise, hypothesis).result(timeout=timeout)
    except FutureTimeout:
        raise VerificationError(claim_id, "NLI call timed out")
    except Exception as exc:
        raise VerificationError(claim_id, f"NLI call failed: {type(exc).__name__}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    try:
        values = {k: float(scores[k]) for k in ("entailment", "neutral", "contradiction")}
    except (KeyError, TypeError, ValueError):
        raise VerificationError(claim_id, "NLI scores are malformed")
    if any(not 0.0 <= v <= 1.0 for v in values.values()):
        raise VerificationError(claim_id, "NLI scores are out of range")
    return values
