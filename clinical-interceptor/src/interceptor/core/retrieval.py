"""Candidate retrieval (PLAN 4.2): the NLI model reads only the triples relevant to a claim, at most k = 5.

Score per triple:
  +3 a claim entity shares its system and code with the triple object
  +3 a claim entity is a general allergy concept and the triple is an allergy (extension: an allergy-concept claim has
     no code in common with a specific allergy, so without this a denial of allergies could never reach the allergy)
  +2 a claim drug has the same drug class as the allergy triple
  +1 for each shared content token between the claim text and the verbalized triple
Candidates with score 0 are discarded. Ties break by source_ref, so results are deterministic.
"""

import re
from dataclasses import dataclass

from interceptor.fhir.triples import Triple, verbalize

from .contracts import CodeSystem

MAX_CANDIDATES = 5
TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "the and for with from that this was were are has have had patient started on of to an in is at by as it "
    "confirmed active final finished status effective observation condition class".split()
)


@dataclass(frozen=True)
class Candidate:
    triple: Triple
    score: int
    reasons: tuple


def content_tokens(text):
    return {t for t in TOKEN.findall(text.lower()) if len(t) >= 3 and t not in STOPWORDS}


def retrieve(claim_text, entities, triples, knowledge, k=MAX_CANDIDATES):
    claim_tokens = content_tokens(claim_text)
    allergy_concept = any(
        e.system is CodeSystem.SNOMED_CT and e.code in knowledge.allergy_concept_codes for e in entities
    )
    drug_classes = {
        knowledge.ingredient_classes[e.code]
        for e in entities
        if e.system is CodeSystem.RXNORM and e.code in knowledge.ingredient_classes
    }
    scored = []
    for triple in triples:
        score, reasons = 0, []
        if triple.object_code and any(
            e.system is triple.object_system and e.code == triple.object_code for e in entities
        ):
            score += 3
            reasons.append("code")
        if allergy_concept and triple.predicate == "HAS_ALLERGY":
            score += 3
            reasons.append("allergy_concept")
        if triple.drug_class and triple.drug_class in drug_classes:
            score += 2
            reasons.append("drug_class")
        shared = claim_tokens & content_tokens(verbalize(triple))
        if shared:
            score += len(shared)
            reasons.append("tokens")
        if score > 0:
            scored.append(Candidate(triple, score, tuple(reasons)))
    scored.sort(key=lambda c: (-c.score, c.triple.source_ref))
    return scored[:k]
