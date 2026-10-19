import unittest
from dataclasses import replace

from core.contracts import CodeSystem
from core.dosage import check_dose, parse_dose
from core.entities import link_entities
from core.knowledge import DoseLimit
from core.retrieval import MAX_CANDIDATES, retrieve
from tests.support import ROOT
from tests.support_verify import KNOWLEDGE, case_triples

NOTE = (ROOT / "seed" / "notes" / "case-0001.txt").read_text(encoding="utf-8")


def link(text, offset=0):
    return link_entities(text, KNOWLEDGE, offset)


class EntityTests(unittest.TestCase):
    def test_case_0001_entities_have_exact_offsets(self):
        found = {e.display: e for e in link(NOTE)}
        drug = found["Drug allergy"]
        self.assertEqual((drug.code, NOTE[drug.char_start:drug.char_end]), ("416098002", "drug allergies"))
        self.assertEqual((drug.char_start, drug.char_end), (69, 83))  # the design doc's 66-80 is wrong for this note
        amox = found["Amoxicillin"]
        self.assertEqual((amox.system, amox.code, amox.char_start, amox.char_end),
                         (CodeSystem.RXNORM, "723", 119, 130))

    def test_every_entity_reproduces_its_surface_text(self):
        for e in link(NOTE):
            self.assertEqual(NOTE[e.char_start:e.char_end], e.surface_text)

    def test_matching_is_case_insensitive_and_keeps_original_case(self):
        entity = link("Started AMOXICILLIN today")[0]
        self.assertEqual(entity.surface_text, "AMOXICILLIN")

    def test_a_surface_inside_a_longer_token_is_not_linked(self):
        self.assertEqual(link("Started amoxicillin-clavulanate"), [])
        self.assertEqual(link("nonamoxicillin"), [])

    def test_longest_surface_wins(self):
        entities = link("hemoglobin a1c was measured")
        self.assertEqual([e.surface_text for e in entities], ["hemoglobin a1c"])

    def test_offsets_are_code_points_not_utf16_units(self):
        text = "\U0001F600 amoxicillin"  # one code point, two UTF-16 units
        self.assertEqual(link(text)[0].char_start, 2)

    def test_offset_argument_shifts_positions(self):
        self.assertEqual(link("amoxicillin", offset=100)[0].char_start, 100)

    def test_no_match_invents_nothing(self):
        self.assertEqual(link("Patient reports headaches"), [])


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.triples = case_triples()

    def refs(self, text):
        return [c.triple.source_ref for c in retrieve(text, link(text), self.triples, KNOWLEDGE)]

    def test_amoxicillin_claim_retrieves_the_penicillin_allergy_by_class(self):
        candidates = retrieve("Started amoxicillin 500 mg", link("Started amoxicillin 500 mg"), self.triples, KNOWLEDGE)
        self.assertEqual(candidates[0].triple.source_ref, "AllergyIntolerance/allergy-penicillin-001")
        self.assertIn("drug_class", candidates[0].reasons)

    def test_t2dm_claim_retrieves_the_condition_first(self):
        self.assertEqual(self.refs("Patient has type 2 diabetes")[0], "Condition/cond-t2dm-001")

    def test_allergy_concept_claim_reaches_the_allergy(self):
        self.assertEqual(self.refs("No known drug allergies")[0], "AllergyIntolerance/allergy-penicillin-001")

    def test_zero_score_candidates_are_discarded(self):
        self.assertEqual(self.refs("Patient reports headaches"), [])

    def test_ties_break_by_source_ref(self):
        twin = replace(self.triples[0], source_ref="AllergyIntolerance/aaa-first")
        candidates = retrieve("Started amoxicillin", link("Started amoxicillin"), [self.triples[0], twin], KNOWLEDGE)
        self.assertEqual([c.triple.source_ref for c in candidates],
                         ["AllergyIntolerance/aaa-first", "AllergyIntolerance/allergy-penicillin-001"])

    def test_at_most_k_candidates(self):
        many = [replace(self.triples[0], source_ref=f"AllergyIntolerance/a{i}") for i in range(9)]
        self.assertEqual(len(retrieve("Started amoxicillin", link("Started amoxicillin"), many, KNOWLEDGE)),
                         MAX_CANDIDATES)

    def test_shared_content_tokens_add_to_the_score(self):
        # "blood" is a content token in the HbA1c triple's display, which is why a blood pressure claim finds it
        self.assertIn("Observation/obs-hba1c-001", self.refs("Blood pressure is stable"))


class DosageTests(unittest.TestCase):
    LIMIT = DoseLimit("test", max_single_mg=1000, max_daily_mg=3000)

    def test_parse_dose_and_frequency(self):
        self.assertEqual(parse_dose("amoxicillin 500 mg TID"), (500.0, 3))
        self.assertEqual(parse_dose("1 g twice daily"), (1000.0, 2))
        self.assertEqual(parse_dose("250 mcg daily"), (0.25, 1))
        self.assertEqual(parse_dose("started amoxicillin"), (None, None))

    def test_single_dose_over_limit(self):
        self.assertEqual(check_dose("amoxicillin 1500 mg once", self.LIMIT), (True, 1000, "mg"))

    def test_daily_total_over_limit(self):
        self.assertEqual(check_dose("amoxicillin 900 mg QID", self.LIMIT), (True, 3000, "mg/day"))

    def test_within_limits(self):
        self.assertEqual(check_dose("amoxicillin 500 mg TID", self.LIMIT), (False, None, None))

    def test_no_dose_is_not_flagged(self):
        self.assertEqual(check_dose("started amoxicillin", self.LIMIT), (False, None, None))


if __name__ == "__main__":
    unittest.main()
