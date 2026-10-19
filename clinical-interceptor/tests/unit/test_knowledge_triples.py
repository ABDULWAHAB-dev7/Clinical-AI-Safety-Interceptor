import copy
import pathlib
import shutil
import tempfile
import unittest

from interceptor.core.contracts import CodeSystem
from interceptor.core.errors import ErrorClass, PipelineError
from interceptor.core.knowledge import KNOWLEDGE_DIR, load_knowledge
from interceptor.fhir.ingest import ingest
from interceptor.fhir.triples import triples_from_ingest, verbalize
from tests.support_verify import KNOWLEDGE, case_triples

PATIENT = {"resourceType": "Patient", "id": "pat-1", "identifier": [{"system": "urn:synthetic:mrn", "value": "X"}],
           "gender": "female", "birthDate": "1968-03-14"}
OBS = {"resourceType": "Observation", "id": "obs-1", "status": "final",
       "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4", "display": "HbA1c"}]},
       "subject": {"reference": "Patient/pat-1"}, "valueQuantity": {"value": 8.2, "unit": "%"}}
COND = {"resourceType": "Condition", "id": "cond-1",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": "44054006", "display": "T2DM"}]},
        "subject": {"reference": "Patient/pat-1"}}


def triples_for(*resources):
    raw = [(r["resourceType"], r["id"], copy.deepcopy(r)) for r in (PATIENT, *resources)]
    return triples_from_ingest(ingest(raw), KNOWLEDGE)


class KnowledgeTests(unittest.TestCase):
    def test_loads_and_sorts_lexicon_longest_first(self):
        lengths = [len(e.surface) for e in KNOWLEDGE.lexicon]
        self.assertEqual(lengths, sorted(lengths, reverse=True))
        self.assertEqual(KNOWLEDGE.thresholds.entailment_min, 0.85)

    def _copy_with(self, filename, text):
        tmp = tempfile.TemporaryDirectory()
        target = pathlib.Path(tmp.name)
        for f in KNOWLEDGE_DIR.iterdir():
            shutil.copy(f, target / f.name)
        (target / filename).write_text(text, encoding="utf-8")
        return tmp, target

    def test_missing_file_refuses_to_load(self):
        tmp, target = self._copy_with("thresholds.yaml", "x: 1\n")
        with tmp, self.assertRaises(PipelineError) as ctx:
            load_knowledge(target)
        self.assertEqual(ctx.exception.error_class, ErrorClass.POLICY_LOAD_FAILURE)

    def test_out_of_range_threshold_refuses_to_load(self):
        tmp, target = self._copy_with(
            "thresholds.yaml",
            "contradiction_min: 1.5\nentailment_min: 0.85\nvalue_tolerance_abs: 0\nnli_timeout_seconds: 5\n")
        with tmp, self.assertRaises(PipelineError):
            load_knowledge(target)

    def test_unknown_lexicon_system_refuses_to_load(self):
        tmp, target = self._copy_with("lexicon_seed.csv", "surface,system,code,display\nfoo,icd10,1,Foo\n")
        with tmp, self.assertRaises(PipelineError):
            load_knowledge(target)


class TripleTests(unittest.TestCase):
    def test_case_0001_yields_exactly_four_triples(self):
        triples = case_triples()
        self.assertEqual(
            sorted((t.predicate, t.source_ref) for t in triples),
            [("HAS_ALLERGY", "AllergyIntolerance/allergy-penicillin-001"),
             ("HAS_CONDITION", "Condition/cond-t2dm-001"),
             ("HAS_ENCOUNTER", "Encounter/enc-0001"),
             ("HAS_OBSERVATION", "Observation/obs-hba1c-001")])

    def test_no_triple_for_gender_or_birth_date(self):
        for t in case_triples():
            self.assertNotIn("female", verbalize(t).lower())
            self.assertNotIn("1968", verbalize(t))

    def test_allergy_triple_carries_drug_class_and_status(self):
        allergy = next(t for t in case_triples() if t.predicate == "HAS_ALLERGY")
        self.assertEqual((allergy.drug_class, allergy.status, allergy.object_code), ("penicillin", "confirmed", "91936005"))
        self.assertIs(allergy.object_system, CodeSystem.SNOMED_CT)

    def test_allergy_premise_text_matches_the_plan_example(self):
        allergy = next(t for t in case_triples() if t.predicate == "HAS_ALLERGY")
        self.assertEqual(verbalize(allergy), "Patient has a confirmed allergy to penicillin (SNOMED 91936005).")

    def test_observation_keeps_value_and_unit(self):
        obs = next(t for t in case_triples() if t.predicate == "HAS_OBSERVATION")
        self.assertEqual((obs.value, obs.numeric_value, obs.unit, obs.status), ("8.2 %", 8.2, "%", "final"))

    def test_entered_in_error_observation_is_not_a_triple(self):
        triples = triples_for(OBS | {"status": "entered-in-error"})
        self.assertEqual(triples, [])

    def test_refuted_condition_is_not_a_triple(self):
        refuted = COND | {"verificationStatus": {"coding": [{"code": "refuted"}]}}
        self.assertEqual(triples_for(refuted), [])

    def test_unmapped_code_keeps_display_but_no_code(self):
        odd = COND | {"code": {"coding": [{"system": "urn:local", "code": "L1", "display": "Local diagnosis"}]}}
        triple = triples_for(odd)[0]
        self.assertIsNone(triple.object_code)
        self.assertEqual(triple.object_display, "Local diagnosis")

    def test_triple_order_is_stable(self):
        self.assertEqual([t.source_ref for t in case_triples()], [t.source_ref for t in case_triples()])


if __name__ == "__main__":
    unittest.main()
