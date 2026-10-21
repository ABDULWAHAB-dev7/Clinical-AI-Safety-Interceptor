"""Regression tests for two false blocks found by the real-note benchmark (docs/VALIDATION_REPORT.md).

1. Negation is scoped to the clause before the entity. "Type 2 diabetes, not at goal" is a correct statement.
2. A contradiction from the NLI model escalates by its high-risk kind only when the candidate was matched by a code.
"""

import unittest

from interceptor.core.contracts import CodeSystem, NLIClass
from interceptor.core.knowledge import load_knowledge
from interceptor.core.verify import verify_claim
from interceptor.fhir.triples import Triple

KNOWLEDGE = load_knowledge()


def diabetes_triple():
    return Triple(subject="Patient/p1", predicate="HAS_CONDITION", object_code="44054006",
                  object_system=CodeSystem.SNOMED_CT, object_display="Diabetes mellitus type 2", value=None,
                  numeric_value=None, unit=None, status="active", effective=None,
                  source_ref="Condition/cond-t2dm", drug_class=None)


class ContradictingScorer:
    """Stands in for the NLI model: returns a fixed high contradiction for every pair."""

    def score(self, premise, hypothesis):
        return {"entailment": 0.005, "neutral": 0.005, "contradiction": 0.99}


def verify(text, scorer=None):
    claim = {"claim_id": "clm_000000000001", "claim_text": text, "char_start": 0}
    result, _ = verify_claim(claim, [diabetes_triple()], KNOWLEDGE, "test", scorer)
    return result


class NegationScopeTests(unittest.TestCase):
    def test_negation_after_the_diagnosis_does_not_deny_it(self):
        result = verify("Type 2 diabetes mellitus, not at goal")
        self.assertEqual(result["nli_class"], NLIClass.ENTAILMENT.value)
        self.assertEqual(result["risk_severity"], "SAFE")

    def test_negation_before_the_diagnosis_denies_it(self):
        result = verify("No history of type 2 diabetes")
        self.assertEqual(result["nli_class"], NLIClass.CONTRADICTION.value)
        self.assertEqual(result["risk_severity"], "CRITICAL")

    def test_negation_in_an_earlier_clause_does_not_reach_the_diagnosis(self):
        result = verify("No fever, type 2 diabetes is documented")
        self.assertEqual(result["nli_class"], NLIClass.ENTAILMENT.value)


class NliCorroborationTests(unittest.TestCase):
    def test_shared_words_alone_give_a_warning_not_a_block(self):
        # "diabetes" appears in the record's verbalized condition, but no coded entity links the claim to it.
        result = verify("Patient reports diabetes related fatigue", scorer=ContradictingScorer())
        self.assertEqual(result["nli_class"], NLIClass.CONTRADICTION.value)
        self.assertEqual(result["risk_severity"], "WARNING")
        self.assertEqual(result["risk_rule"], "contradiction")


if __name__ == "__main__":
    unittest.main()
