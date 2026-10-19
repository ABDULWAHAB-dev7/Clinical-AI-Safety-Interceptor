"""Shared fixtures for verifier tests: knowledge, the case-0001 triples, and a scriptable fake NLI model."""

import dataclasses

from core.knowledge import load_knowledge
from fhir.ingest import ingest, load_ndjson
from fhir.triples import triples_from_ingest
from tests.support import ROOT

KNOWLEDGE = load_knowledge()
SEED_FHIR = ROOT / "seed" / "fhir"


def case_triples(knowledge=KNOWLEDGE):
    return triples_from_ingest(ingest(load_ndjson(SEED_FHIR)), knowledge)


def with_thresholds(knowledge=KNOWLEDGE, **changes):
    return dataclasses.replace(knowledge, thresholds=dataclasses.replace(knowledge.thresholds, **changes))


class FakeNli:
    """Returns fixed scores, or per-premise scores. `delay` simulates a slow model."""

    def __init__(self, default=None, by_source=None, delay=0.0, error=None):
        self.default = default or {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1}
        self.by_source = by_source or {}
        self.delay = delay
        self.error = error
        self.calls = []

    def score(self, premise, hypothesis):
        import time

        self.calls.append((premise, hypothesis))
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        for needle, scores in self.by_source.items():
            if needle in premise:
                return scores
        return self.default
