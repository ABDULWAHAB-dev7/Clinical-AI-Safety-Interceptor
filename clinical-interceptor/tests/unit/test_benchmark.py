import json
import tempfile
import unittest
from pathlib import Path

from benchmark import metrics
from benchmark.generator.generate import corpus_digest, write_corpus
from benchmark.generator.validate import validate
from tests.support import ROOT

CORPUS = ROOT / "benchmark" / "corpus" / "v1"


def statement(start, end, risk, nli="CONTRADICTION", rule="contradiction_high_risk", ref=None, category="X"):
    return {"char_start": start, "char_end": end, "expected_risk": risk, "expected_nli": nli,
            "expected_risk_rule": rule, "expected_ref": ref, "expected_category": category}


def claim(claim_id, start, end, risk, nli="CONTRADICTION", rule="contradiction_high_risk", ref=None):
    return {"claim_id": claim_id, "char_start": start, "char_end": end, "risk_severity": risk, "nli_class": nli,
            "risk_rule": rule, "matched_fhir_ref": ref}


def note(note_id, label, gold, predicted, events=(), error=None, status="BLOCKED"):
    return {"note_id": note_id, "label": label, "split": "test", "gold": gold, "predicted": predicted,
            "events": list(events), "error": error, "pipeline_status": None if error else status,
            "latency_ms": {"verify": 1.0}}


class HandComputedMetricsTest(unittest.TestCase):
    """Four gold claims with known answers. CRITICAL recall 1/2, CRITICAL precision 1/2, flag recall 3/3, flag
    precision 3/3."""

    def setUp(self):
        gold = [
            statement(0, 10, "CRITICAL"),   # g1: caught as CRITICAL
            statement(20, 30, "CRITICAL"),  # g2: missed as WARNING
            statement(40, 50, "WARNING", "contradiction", "contradiction", "X"),  # g3: over-rated as CRITICAL
            statement(60, 70, "SAFE", "ENTAILMENT", "entailed", None),  # g4: correctly SAFE
        ]
        predicted = [
            claim("c1", 0, 10, "CRITICAL"),
            claim("c2", 20, 30, "WARNING", rule="contradiction"),
            claim("c3", 40, 50, "CRITICAL"),
            claim("c4", 60, 70, "SAFE", "ENTAILMENT", "entailed"),
        ]
        self.records = [note("n1", "halluc", gold, predicted)]

    def test_critical_recall_and_precision(self):
        rates = metrics.derive(metrics.aggregate(metrics.note_counts(r) for r in self.records))
        self.assertAlmostEqual(rates["critical_recall"], 0.5)
        self.assertAlmostEqual(rates["critical_precision"], 0.5)

    def test_flag_recall_and_precision(self):
        rates = metrics.derive(metrics.aggregate(metrics.note_counts(r) for r in self.records))
        self.assertAlmostEqual(rates["flag_recall"], 1.0)
        self.assertAlmostEqual(rates["flag_precision"], 1.0)

    def test_severity_matrix_records_the_one_over_rating(self):
        counts = metrics.note_counts(self.records[0])
        self.assertEqual(counts["sev|WARNING|CRITICAL"], 1)
        self.assertEqual(counts["sev|CRITICAL|WARNING"], 1)

    def test_bootstrap_is_reproducible_and_inside_the_unit_interval(self):
        per_note = [metrics.note_counts(r) for r in self.records]
        first = metrics.bootstrap(per_note, ["critical_recall"], samples=50)
        second = metrics.bootstrap(per_note, ["critical_recall"], samples=50)
        self.assertEqual(first, second)
        low, high = first["critical_recall"]
        self.assertTrue(0.0 <= low <= high <= 1.0)


class CleanNoteTest(unittest.TestCase):
    def test_clean_note_flag_rate_counts_notes_with_any_flagged_claim(self):
        clean_ok = note("c1", "clean", [statement(0, 5, "SAFE", "ENTAILMENT", "entailed")],
                        [claim("a", 0, 5, "SAFE", "ENTAILMENT", "entailed")])
        clean_bad = note("c2", "clean", [statement(0, 5, "SAFE", "ENTAILMENT", "entailed")],
                         [claim("b", 0, 5, "WARNING", "NEUTRAL", "unsupported_clinical")])
        rates = metrics.derive(metrics.aggregate(metrics.note_counts(r) for r in [clean_ok, clean_bad]))
        self.assertAlmostEqual(rates["clean_note_flag_rate"], 0.5)

    def test_an_unmatched_flagged_claim_is_a_false_positive_for_precision(self):
        record = note("h1", "halluc", [statement(0, 5, "CRITICAL")],
                      [claim("a", 0, 5, "CRITICAL"), claim("b", 50, 60, "CRITICAL")])
        rates = metrics.derive(metrics.aggregate([metrics.note_counts(record)]))
        self.assertAlmostEqual(rates["critical_precision"], 0.5)
        self.assertAlmostEqual(rates["critical_recall"], 1.0)

    def test_a_failed_note_counts_as_misses_and_is_reported(self):
        record = note("h2", "halluc", [statement(0, 5, "CRITICAL")], [], error="INTERCEPT_VERIFIER_UNAVAILABLE")
        rates = metrics.derive(metrics.aggregate([metrics.note_counts(record)]))
        self.assertEqual(rates["critical_recall"], 0.0)
        self.assertEqual(rates["note_error_rate"], 1.0)


class GateTest(unittest.TestCase):
    def test_bg2_fails_below_0_80_and_nli_is_not_evaluable(self):
        gates = metrics.gate_status({"critical_recall": 0.79, "clean_note_flag_rate": 0.0, "nli_macro_f1": None})
        self.assertEqual(gates["BG2"]["status"], "fail")
        self.assertEqual(gates["BG3"]["status"], "pass")
        self.assertEqual(gates["BG1"]["status"], "not_evaluable")


class CorpusTest(unittest.TestCase):
    def test_committed_corpus_is_valid(self):
        self.assertEqual(validate(CORPUS), [])

    def test_generation_is_deterministic_for_a_seed(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            write_corpus(first, 7, 5, 5)
            write_corpus(second, 7, 5, 5)
            self.assertEqual(corpus_digest(first), corpus_digest(second))

    def test_validate_catches_a_moved_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "corpus"
            write_corpus(out, 7, 3, 3)
            gold_path = sorted((out / "gold").glob("*.gold.json"))[0]
            gold = json.loads(gold_path.read_text(encoding="utf-8"))
            gold["propositions"][0]["char_start"] += 1
            gold_path.write_text(json.dumps(gold), encoding="utf-8")
            self.assertTrue(any("offsets" in error for error in validate(out)))


if __name__ == "__main__":
    unittest.main()
