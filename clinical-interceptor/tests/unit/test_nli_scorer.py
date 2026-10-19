import json
import pathlib
import tempfile
import unittest

from interceptor.core.errors import ErrorClass, PipelineError
from interceptor.core.verify import verify_claim
from interceptor.nli.scorer import NLIScorer, directory_sha256
from tests.support_verify import KNOWLEDGE, case_triples


class FakeCrossEncoder:
    """Stands in for sentence_transformers.CrossEncoder: returns logits in the model's own label order."""

    def __init__(self, logits, path=None):
        self.logits, self.seen = logits, []

    def predict(self, pairs):
        self.seen.extend(pairs)
        return [self.logits]


def model_dir(id2label=None, extra_file=b"weights"):
    tmp = tempfile.TemporaryDirectory()
    path = pathlib.Path(tmp.name)
    labels = id2label or {"0": "contradiction", "1": "entailment", "2": "neutral"}
    (path / "config.json").write_text(json.dumps({"id2label": labels}), encoding="utf-8")
    (path / "model.bin").write_bytes(extra_file)
    return tmp, path


def scorer(path, logits=(0.1, 5.0, 0.2), lock=None):
    return NLIScorer(path, lock_path=lock, cross_encoder_factory=lambda p: FakeCrossEncoder(list(logits), p))


class LabelMapTests(unittest.TestCase):
    def test_label_order_comes_from_the_model_config_not_an_assumption(self):
        tmp, path = model_dir({"0": "contradiction", "1": "entailment", "2": "neutral"})
        with tmp:
            scores = scorer(path, logits=(0.1, 5.0, 0.2)).score("p", "h")
        self.assertEqual(max(scores, key=scores.get), "entailment")  # index 1 is entailment in this model

    def test_a_different_label_order_gives_a_different_mapping(self):
        tmp, path = model_dir({"0": "entailment", "1": "neutral", "2": "contradiction"})
        with tmp:
            scores = scorer(path, logits=(0.1, 5.0, 0.2)).score("p", "h")
        self.assertEqual(max(scores, key=scores.get), "neutral")

    def test_labels_are_case_insensitive(self):
        tmp, path = model_dir({"0": "CONTRADICTION", "1": "Entailment", "2": "NEUTRAL"})
        with tmp:
            self.assertEqual(set(scorer(path).score("p", "h")), {"entailment", "neutral", "contradiction"})

    def test_scores_are_probabilities_that_sum_to_one(self):
        tmp, path = model_dir()
        with tmp:
            scores = scorer(path, logits=(2.0, -1.0, 0.5)).score("p", "h")
        self.assertAlmostEqual(sum(scores.values()), 1.0)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in scores.values()))

    def test_outputs_that_are_already_probabilities_are_kept(self):
        tmp, path = model_dir()
        with tmp:
            scores = scorer(path, logits=(0.2, 0.7, 0.1)).score("p", "h")
        self.assertEqual(scores, {"contradiction": 0.2, "entailment": 0.7, "neutral": 0.1})

    def test_labels_that_are_not_the_three_expected_are_refused(self):
        for labels in ({"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2"},
                       {"0": "entailment", "1": "neutral"},
                       {"0": "entailment", "1": "entailment", "2": "neutral"}):
            tmp, path = model_dir(labels)
            with tmp, self.subTest(labels=labels), self.assertRaises(PipelineError) as ctx:
                scorer(path)
            self.assertEqual(ctx.exception.error_class, ErrorClass.POLICY_LOAD_FAILURE)

    def test_a_missing_config_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(PipelineError):
            scorer(tmp)

    def test_malformed_model_output_raises_so_the_verifier_fails_closed(self):
        tmp, path = model_dir()
        with tmp:
            bad = scorer(path, logits=(0.1, 0.2))
            with self.assertRaises(ValueError):
                bad.score("p", "h")


class LockFileTests(unittest.TestCase):
    def write_lock(self, directory, sha):
        lock = pathlib.Path(directory) / "MODEL.lock.json"
        lock.write_text(json.dumps({"sha256": sha}), encoding="utf-8")
        return lock

    def test_matching_hash_loads(self):
        tmp, path = model_dir()
        with tmp, tempfile.TemporaryDirectory() as lock_dir:
            scorer(path, lock=self.write_lock(lock_dir, directory_sha256(path)))

    def test_changed_weights_are_refused(self):
        tmp, path = model_dir()
        with tmp, tempfile.TemporaryDirectory() as lock_dir:
            lock = self.write_lock(lock_dir, directory_sha256(path))
            (path / "model.bin").write_bytes(b"tampered")
            with self.assertRaises(PipelineError):
                scorer(path, lock=lock)

    def test_a_missing_lock_file_is_refused(self):
        tmp, path = model_dir()
        with tmp, self.assertRaises(PipelineError):
            scorer(path, lock=path / "nope.json")

    def test_hash_depends_on_file_names_and_content(self):
        tmp_a, a = model_dir()
        tmp_b, b = model_dir(extra_file=b"other")
        with tmp_a, tmp_b:
            self.assertNotEqual(directory_sha256(a), directory_sha256(b))
            self.assertEqual(directory_sha256(a), directory_sha256(a))


class WarmUpAndIntegrationTests(unittest.TestCase):
    def test_warm_up_passes_for_a_model_that_recognizes_identical_sentences(self):
        tmp, path = model_dir()
        with tmp:
            scorer(path, logits=(0.1, 5.0, 0.2)).warm_up()

    def test_warm_up_refuses_a_model_whose_labels_are_wrong(self):
        tmp, path = model_dir()
        with tmp, self.assertRaises(PipelineError):
            scorer(path, logits=(5.0, 0.1, 0.2)).warm_up()  # scores identical sentences as a contradiction

    def test_the_scorer_resolves_a_claim_the_deterministic_verifier_cannot(self):
        tmp, path = model_dir({"0": "entailment", "1": "neutral", "2": "contradiction"})
        with tmp:
            nli = scorer(path, logits=(0.1, 5.0, 0.2))  # neutral
            claim = {"claim_id": "clm_000000000001", "claim_text": "Blood pressure is stable"}
            result, trace = verify_claim(claim, case_triples(), KNOWLEDGE, "t", nli)
        self.assertEqual((result["nli_class"], result["risk_rule"]), ("NEUTRAL", "unsupported_clinical"))
        self.assertEqual(trace["branch"], "nli_neutral")

    def test_the_model_library_is_not_imported_unless_the_default_factory_is_used(self):
        import sys

        self.assertNotIn("sentence_transformers", sys.modules)


if __name__ == "__main__":
    unittest.main()
