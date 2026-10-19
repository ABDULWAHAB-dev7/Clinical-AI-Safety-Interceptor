import unittest

from core.contracts import NLIClass
from intercept.analysis import SAFE_GAP, analyze, verify_unit
from intercept.policy import (
    ANNOTATED,
    BLOCK,
    REDACTED,
    RELEASED,
    WITHHELD,
    finish_reason,
    marker_for,
    render,
    response_status,
    sentence_action,
)
from tests.support_proxy import OUTPUT
from tests.support_verify import KNOWLEDGE, case_triples

SHA = "a" * 64


class ReleasePolicyTableTests(unittest.TestCase):
    """The pass, redact and block table (PLAN 7.5.1), one row per case."""

    def test_safe_is_released(self):
        self.assertEqual(sentence_action("SAFE"), RELEASED)

    def test_warning_is_annotated_by_default_and_redacted_on_request(self):
        self.assertEqual(sentence_action("WARNING", "ANNOTATE"), ANNOTATED)
        self.assertEqual(sentence_action("WARNING", "REDACT_SENTENCE"), REDACTED)

    def test_critical_is_redacted_by_default_and_blocks_on_request(self):
        self.assertEqual(sentence_action("CRITICAL", critical_action="REDACT_SENTENCE"), REDACTED)
        self.assertEqual(sentence_action("CRITICAL", critical_action="BLOCK_RESPONSE"), BLOCK)

    def test_unverified_always_blocks(self):
        for warning in ("ANNOTATE", "REDACT_SENTENCE"):
            for critical in ("REDACT_SENTENCE", "BLOCK_RESPONSE"):
                self.assertEqual(sentence_action("UNVERIFIED", warning, critical), BLOCK)

    def test_an_unknown_severity_never_releases(self):
        self.assertEqual(sentence_action("SOMETHING-NEW"), BLOCK)

    def test_withheld_sentence(self):
        self.assertEqual(sentence_action("WITHHELD"), WITHHELD)

    def test_markers(self):
        self.assertEqual(marker_for(REDACTED, "CRITICAL"), "[REDACTED: CRITICAL]")
        self.assertEqual(marker_for(REDACTED, "WARNING"), "[REDACTED: WARNING]")
        self.assertEqual(marker_for(WITHHELD, "WITHHELD"), "[WITHHELD: VERIFICATION_TIMEOUT]")
        self.assertIsNone(marker_for(RELEASED, "SAFE"))
        self.assertIsNone(marker_for(ANNOTATED, "WARNING"))

    def test_status_precedence_blocked_over_redacted_over_released(self):
        self.assertEqual(response_status([RELEASED, ANNOTATED]), "RELEASED")
        self.assertEqual(response_status([RELEASED, REDACTED]), "REDACTED")
        self.assertEqual(response_status([WITHHELD]), "REDACTED")
        self.assertEqual(response_status([REDACTED, BLOCK, RELEASED]), "BLOCKED")

    def test_finish_reason_by_status(self):
        self.assertEqual(finish_reason("RELEASED", "length"), "length")
        self.assertEqual(finish_reason("RELEASED", None), "stop")
        self.assertEqual(finish_reason("REDACTED", "length"), "stop")
        self.assertEqual(finish_reason("BLOCKED", "stop"), "content_filter")

    def test_render_replaces_only_marked_spans(self):
        text = "One. Two. Three."
        self.assertEqual(render(text, [(0, 4, None), (5, 9, "[X]"), (10, 16, None)]), "One. [X] Three.")

    def test_render_keeps_text_between_spans(self):
        self.assertEqual(render("Labs: A.\nB.", [(6, 8, "[R]"), (9, 11, None)]), "Labs: [R]\nB.")


class AnalysisTests(unittest.TestCase):
    def units(self, text):
        return analyze(text, SHA, KNOWLEDGE)

    def test_sentences_become_units_with_exact_offsets(self):
        units = self.units(OUTPUT)
        self.assertEqual(len(units), 3)
        for unit in units:
            self.assertEqual(OUTPUT[unit.start:unit.end], unit.claim["source_sentence"])
        self.assertEqual((units[0].start, units[0].end), (0, 36))

    def test_claim_ids_follow_the_spec_derivation(self):
        from core.claims import claim_id_for

        unit = self.units(OUTPUT)[0]
        claim = unit.claim
        self.assertEqual(claim["claim_id"],
                         claim_id_for(SHA, claim["char_start"], claim["char_end"], claim["claim_text"]))

    def test_entities_are_linked_with_note_offsets(self):
        unit = self.units(OUTPUT)[2]
        amox = next(e for e in unit.claim["entities"] if e.code == "723")
        self.assertEqual(OUTPUT[amox.char_start:amox.char_end], "amoxicillin")

    def test_a_sentence_too_long_for_a_claim_is_an_unverified_unit(self):
        long_sentence = "x" * 600 + "."
        unit = self.units(long_sentence)[0]
        self.assertIsNone(unit.claim)
        self.assertEqual(unit.reason, "no_usable_claim")

    def test_safe_gaps(self):
        for gap in ("", " ", "\n", " .", "Labs: ", "\nAllergies: ", "Plan:\n"):
            self.assertTrue(SAFE_GAP.match(gap), repr(gap))

    def test_text_between_sentences_that_is_not_a_gap_is_not_safe(self):
        for gap in (" cure ", "Note: take insulin ", "x"):
            self.assertFalse(SAFE_GAP.match(gap), repr(gap))

    def test_section_headers_are_safe_gaps_not_unverified_units(self):
        units = self.units("Allergies: penicillin.\nLabs: HbA1c 8.2%.")
        self.assertTrue(all(u.claim is not None for u in units))

    def test_empty_text_has_no_units(self):
        self.assertEqual(self.units(""), [])
        self.assertEqual(self.units("   \n"), [])


class VerifyUnitTests(unittest.TestCase):
    TRIPLES = case_triples()

    def verdict(self, sentence):
        unit = analyze(sentence, SHA, KNOWLEDGE)[0]
        return verify_unit(unit, self.TRIPLES, KNOWLEDGE, "t")

    def test_severities(self):
        self.assertEqual(self.verdict("Patient denies known drug allergies.").severity, "CRITICAL")
        self.assertEqual(self.verdict("HbA1c 8.2% on 2026-09-02.").severity, "SAFE")
        self.assertEqual(self.verdict("Patient reports headaches.").severity, "WARNING")

    def test_a_claim_that_needs_nli_is_unverified_not_an_exception(self):
        verdict = self.verdict("Blood pressure is stable.")
        self.assertEqual(verdict.severity, "UNVERIFIED")
        self.assertEqual(verdict.trace["branch"], "verification_error")

    def test_a_unit_with_no_claim_is_unverified(self):
        unit = analyze("x" * 600 + ".", SHA, KNOWLEDGE)[0]
        self.assertEqual(verify_unit(unit, self.TRIPLES, KNOWLEDGE, "t").severity, "UNVERIFIED")

    def test_result_is_a_valid_verification_result(self):
        result = self.verdict("Patient denies known drug allergies.").result
        self.assertEqual(NLIClass(result["nli_class"]), NLIClass.CONTRADICTION)


if __name__ == "__main__":
    unittest.main()
