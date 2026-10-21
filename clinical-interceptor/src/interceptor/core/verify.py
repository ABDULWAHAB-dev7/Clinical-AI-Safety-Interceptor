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

Negation (validation 2026-10-21): a negation cue governs an entity only when it comes before the entity in the same
clause, so "Type 2 diabetes, not at goal" keeps the diagnosis affirmed.
NLI contradictions (validation 2026-10-21): a contradiction can raise severity by its high-risk kind only when the
candidate was matched by a code, an allergy concept, or a drug class. A match on shared words alone stays a WARNING.
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, cast

from pydantic import ValidationError

from interceptor.fhir.triples import Triple, verbalize

from .contracts import CodeSystem, NLIClass, VerificationResult
from .decompose import NEGATION
from .dosage import check_dose
from .entities import Entity, link_entities
from .knowledge import Knowledge
from .retrieval import Candidate, retrieve
from .severity import ClaimFacts, classify_risk

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
CLAUSE_BOUNDARY = re.compile(r"[,;:.!?]")
CODED_REASONS = frozenset({"code", "allergy_concept", "drug_class"})


class NliScorer(Protocol):
    """The NLI model the verifier calls: premise and hypothesis in, a probability per label out."""

    def score(self, premise: str, hypothesis: str) -> Mapping[str, float]:
        ...


class BatchNliScorer(Protocol):
    """A scorer that can answer several pairs in one call. The local and the remote scorer both can."""

    def score_batch(self, pairs: Sequence[tuple[str, str]]) -> Sequence[Mapping[str, float]]:
        ...


class VerificationError(Exception):
    """A claim cannot be verified. The caller must fail closed and never default a severity."""

    def __init__(self, claim_id: str, reason: str) -> None:
        super().__init__(f"{claim_id}: {reason}")
        self.claim_id = claim_id
        self.reason = reason


@dataclass(frozen=True)
class Decision:
    nli_class: NLIClass
    confidence: float
    ref: Optional[str]
    branch: str
    kinds: frozenset[str] = frozenset()
    is_medication: bool = False
    matches_allergy: bool = False


@dataclass
class VerifyOutcome:
    results: list[dict[str, Any]] = field(default_factory=list)
    traces: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Prepared:
    """A claim with everything decided except, possibly, its NLI scores."""
    claim_id: str
    text: str
    candidates: list[Candidate]
    decision: Optional[Decision]  # None when the claim needs the NLI model
    trace: dict[str, Any]


def verify_claims(claims: Sequence[dict[str, Any]], triples: Sequence[Triple], knowledge: Knowledge,
                  verifier_version: str, nli_model: Optional[NliScorer] = None) -> VerifyOutcome:
    """Verifies every claim, with one batched NLI call for all the claims that need the model.

    The batch matters: the latency budget is for all NLI-routed claims in a note, and one forward pass over a
    batch costs less per pair than one pass per pair (docs/gates/G1-measurements.json: 149.8 ms for a single pair
    against 74.8 ms per pair at batch size 4).
    """
    outcome = VerifyOutcome()
    prepared = [_prepare(claim, triples, knowledge) for claim in claims]
    scores = _score_pending([p for p in prepared if p.decision is None], knowledge, nli_model)
    for item in prepared:
        result, trace = _finish(item, scores.get(item.claim_id), knowledge, verifier_version)
        outcome.results.append(result)
        outcome.traces.append(trace)
    return outcome


def verify_claim(claim: dict[str, Any], triples: Sequence[Triple], knowledge: Knowledge, verifier_version: str,
                 nli_model: Optional[NliScorer] = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """One claim. The result is the same as verify_claims on a single-claim list."""
    outcome = verify_claims([claim], triples, knowledge, verifier_version, nli_model)
    return outcome.results[0], outcome.traces[0]


def _prepare(claim: dict[str, Any], triples: Sequence[Triple], knowledge: Knowledge) -> _Prepared:
    """Everything that does not need the NLI model: the entities, the candidates, and the branch that applies."""
    claim_id, text = claim["claim_id"], claim["claim_text"]
    entities = list(claim.get("entities") or link_entities(text, knowledge, offset=claim.get("char_start", 0)))
    candidates = retrieve(text, entities, triples, knowledge)
    trace: dict[str, Any] = {
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
    allergy_negated = any(_negation_governs(text, e) for e in allergy_entities)

    decision: Optional[Decision]
    if rx:
        decision = _medication(text, rx, allergy_triples, knowledge, trace)
    elif allergy_entities:
        decision = _allergy_concept(allergy_triples, allergy_negated)
    else:
        coded_decision = _coded_match(text, coded, triples, knowledge)
        if coded_decision is not None:
            decision = coded_decision
        elif candidates:
            decision = None  # the NLI model settles this one
        else:
            decision = Decision(NLIClass.NEUTRAL, 1.0, None, "no_candidates")
    trace["branch"] = decision.branch if decision is not None else None
    return _Prepared(claim_id, text, candidates, decision, trace)


def _finish(item: _Prepared, scores: Optional[list[dict[str, float]]], knowledge: Knowledge,
            verifier_version: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The NLI decision if the claim needed one, then the severity rule, then the result."""
    decision = item.decision
    if decision is None:
        decision = _nli_decision(item.claim_id, item.text, item.candidates, scores or [], knowledge)
    item.trace["branch"] = decision.branch

    facts = ClaimFacts(
        nli_class=decision.nli_class,
        confidence=decision.confidence,
        is_clinical=True,
        is_medication_claim=decision.is_medication,
        matches_allergy=decision.matches_allergy,
        entity_kinds=decision.kinds,
    )
    severity, rule = classify_risk(facts, entailment_floor=knowledge.thresholds.entailment_min,
                                   contradiction_critical_min=knowledge.thresholds.contradiction_critical_min)
    try:
        result = VerificationResult(
            result_id=uuid.uuid4(),
            claim_id=item.claim_id,
            matched_fhir_ref=decision.ref,
            nli_class=decision.nli_class,
            confidence=min(max(decision.confidence, 0.0), 1.0),
            risk_severity=severity,
            risk_rule=rule,
            verifier_version=verifier_version,
        )
    except ValidationError:
        raise VerificationError(item.claim_id, "result is inconsistent with the VerificationResult contract")
    return result.model_dump(mode="json"), item.trace


def _score_pending(pending: Sequence[_Prepared], knowledge: Knowledge,
                    nli_model: Optional[NliScorer]) -> dict[str, list[dict[str, float]]]:
    """Scores every (claim, candidate) pair that needs the model. One batch, one timeout, fail closed."""
    if not pending:
        return {}
    if nli_model is None:
        raise VerificationError(pending[0].claim_id,
                                "unresolved candidates need the NLI scorer, which is not available")
    timeout = knowledge.thresholds.nli_timeout_seconds
    pairs = [(verbalize(candidate.triple), item.text) for item in pending for candidate in item.candidates]
    if hasattr(nli_model, "score_batch"):
        rows = _score_batch(pairs, cast("BatchNliScorer", nli_model), timeout, pending[0].claim_id)
    else:
        rows = [_score(pending[0].claim_id, nli_model, premise, hypothesis, timeout) for premise, hypothesis in pairs]
    scores: dict[str, list[dict[str, float]]] = {}
    index = 0
    for item in pending:
        count = len(item.candidates)
        scores[item.claim_id] = rows[index:index + count]
        index += count
    return scores


def _negation_governs(text: str, entity: Entity) -> bool:
    """A negation cue governs an entity only when it comes before the entity, in the same clause."""
    position = text.lower().find(entity.surface_text.lower())
    if position < 0:
        return False
    clause = CLAUSE_BOUNDARY.split(text[:position])[-1]
    return NEGATION.search(clause) is not None


def _medication(text: str, rx: Sequence[Entity], allergy_triples: Sequence[Triple], knowledge: Knowledge,
                trace: dict[str, Any]) -> Decision:
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


def _allergy_concept(allergy_triples: Sequence[Triple], negated: bool) -> Decision:
    if not allergy_triples:
        return Decision(NLIClass.NEUTRAL, 1.0, None, "no_allergy_record")  # absence is not evidence of no allergy
    ref = allergy_triples[0].source_ref
    if negated:
        return Decision(NLIClass.CONTRADICTION, 1.0, ref, "allergy_denied_but_documented", frozenset({"allergy"}))
    return Decision(NLIClass.ENTAILMENT, 1.0, ref, "allergy_documented")


def _coded_match(text: str, coded: Sequence[Entity], triples: Sequence[Triple], knowledge: Knowledge) -> Optional[Decision]:
    for entity in sorted(coded, key=lambda e: e.char_start):
        matches = sorted((t for t in triples if t.object_code == entity.code and t.object_system is entity.system),
                         key=lambda t: t.source_ref)
        if not matches:
            continue
        triple = matches[0]
        negated = _negation_governs(text, entity)
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


def _claimed_number(text: str, entity: Entity) -> Optional[float]:
    """First number after the entity in the claim, ignoring ISO dates."""
    blanked = DATE.sub(lambda m: " " * len(m.group()), text)
    start = max(blanked.lower().find(entity.surface_text.lower()), 0)
    match = NUMBER.search(blanked, start + len(entity.surface_text))
    return float(match.group()) if match else None


def _nli_decision(claim_id: str, text: str, candidates: Sequence[Candidate], scores: Sequence[Mapping[str, float]],
                  knowledge: Knowledge) -> Decision:
    """The NLI branch, with the scores already computed. Contradiction outranks entailment."""
    thresholds = knowledge.thresholds
    contradiction: Optional[tuple[float, Candidate]] = None
    best: Optional[tuple[float, float, float, Candidate]] = None
    for candidate, candidate_scores in zip(candidates, scores):
        entailment, neutral, contra = (candidate_scores["entailment"], candidate_scores["neutral"],
                                        candidate_scores["contradiction"])
        if contra >= thresholds.contradiction_min and (contradiction is None or contra > contradiction[0]):
            contradiction = (contra, candidate)  # contradiction takes precedence over entailment
        if best is None or entailment > best[0]:
            best = (entailment, neutral, contra, candidate)
    if contradiction is not None:
        contra, candidate = contradiction
        corroborated = bool(CODED_REASONS & set(candidate.reasons))  # shared words alone never raise severity
        kinds: set[str] = set()
        if corroborated:
            kinds = {"allergy"} if candidate.triple.predicate == "HAS_ALLERGY" else (
                {"active_diagnosis"} if candidate.triple.predicate == "HAS_CONDITION"
                and candidate.triple.status == "active" else set())
        return Decision(NLIClass.CONTRADICTION, contra, candidate.triple.source_ref, "nli_contradiction",
                        frozenset(kinds))
    if best is None:  # candidates is never empty here (_prepare checks it); fail closed if that ever changes
        raise VerificationError(claim_id, "no candidate was scored")
    entailment, neutral, contra, candidate = best
    if entailment >= max(neutral, contra):
        return Decision(NLIClass.ENTAILMENT, entailment, candidate.triple.source_ref, "nli_entailment")
    return Decision(NLIClass.NEUTRAL, max(neutral, contra), None, "nli_neutral")


def _score_batch(pairs: Sequence[tuple[str, str]], model: BatchNliScorer, timeout: float,
                 claim_id: str) -> list[dict[str, float]]:
    """One call for every pair, one timeout for the whole batch. Any failure fails closed."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        rows = executor.submit(model.score_batch, list(pairs)).result(timeout=timeout)
    except FutureTimeout:
        raise VerificationError(claim_id, "NLI call timed out")
    except Exception as exc:
        raise VerificationError(claim_id, f"NLI call failed: {type(exc).__name__}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    if not isinstance(rows, list) or len(rows) != len(pairs):
        raise VerificationError(claim_id, "NLI returned the wrong number of scores")
    validated = []
    for row in rows:
        try:
            values = {k: float(row[k]) for k in ("entailment", "neutral", "contradiction")}
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationError(claim_id, "NLI scores are malformed") from exc
        if any(not 0.0 <= v <= 1.0 for v in values.values()):
            raise VerificationError(claim_id, "NLI scores are out of range")
        validated.append(values)
    return validated


def _score(claim_id: str, model: NliScorer, premise: str, hypothesis: str, timeout: float) -> dict[str, float]:
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
