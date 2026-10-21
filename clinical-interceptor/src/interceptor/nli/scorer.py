"""Local NLI scorer (PLAN 4.1). Loads a cross-encoder from a directory on disk; nothing is downloaded or called remotely.

No checkpoint is chosen or bundled: that is Gate G1 (a human approves the model and its license). This adapter only
loads the directory it is pointed at, and refuses to run if anything about it is not as expected:

  * the label order is read from the model's config.json (id2label), never assumed, and the labels must be exactly
    entailment, neutral and contradiction;
  * if a lock file is given, the SHA-256 of the model directory must match it (supply-chain check, architecture 8.6);
  * the model library is imported only here, so no other module loads model weights.

score(premise, hypothesis) returns {"entailment": p, "neutral": p, "contradiction": p}, which core.verify validates.
"""

import hashlib
import json
import math
from pathlib import Path

from interceptor.core.errors import ErrorClass, PipelineError

LABELS = frozenset({"entailment", "neutral", "contradiction"})


def directory_sha256(path):
    """Hash of every file in the directory (relative path and content), in a stable order.

    The order is the POSIX relative path, compared as a byte string. Sorting the Path objects themselves would not
    do: PurePath compares case-insensitively on Windows and case-sensitively on POSIX, so the same directory would
    hash differently on a developer machine and on the Linux host that runs the service, and the lock file would
    pass on one platform and fail on the other.
    """
    digest = hashlib.sha256()
    root = Path(path)
    files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix())
    for file in files:
        digest.update(file.relative_to(root).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def _default_factory(model_path):
    from sentence_transformers import CrossEncoder  # heavy import, deliberately lazy and only here

    return CrossEncoder(model_path, device="cpu")


class NLIScorer:
    def __init__(self, model_path, lock_path=None, cross_encoder_factory=None):
        path = Path(model_path)
        try:
            config = json.loads((path / "config.json").read_text(encoding="utf-8"))
            id2label = {int(k): str(v).lower() for k, v in config["id2label"].items()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "NLI model config is missing or unreadable")
        if sorted(id2label) != [0, 1, 2] or set(id2label.values()) != LABELS:
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "NLI model labels are not entailment, neutral, contradiction")
        if lock_path is not None:
            try:
                expected = json.loads(Path(lock_path).read_text(encoding="utf-8"))["sha256"]
            except (OSError, ValueError, KeyError, TypeError):
                raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "NLI model lock file is missing or unreadable")
            if directory_sha256(path) != expected:
                raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "NLI model files do not match the lock file")
        try:
            self._model = (cross_encoder_factory or _default_factory)(str(path))
        except Exception as exc:
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, f"NLI model failed to load: {type(exc).__name__}")
        self._order = [id2label[i] for i in range(3)]

    def score(self, premise, hypothesis):
        return self.score_batch([(premise, hypothesis)])[0]

    def score_batch(self, pairs):
        """Scores several premise/hypothesis pairs in one forward pass.

        One pass over a batch costs less per pair than one pass per pair (G1-measurements.json: 149.8 ms for a
        single pair against 74.8 ms per pair at batch size 4), and the verifier's latency budget is for all
        NLI-routed claims in a note, not for each one.
        """
        pairs = list(pairs)
        if not pairs:
            return []
        raw = self._model.predict(pairs)
        results = []
        for row in raw:
            values = [float(v) for v in row]
            if len(values) != 3 or any(math.isnan(v) or math.isinf(v) for v in values):
                raise ValueError("unexpected model output")
            results.append(dict(zip(self._order, _probabilities(values))))
        return results

    def warm_up(self):
        """One scoring call at startup, so the first request does not pay for it and a broken model fails early."""
        scores = self.score("The patient has a fever.", "The patient has a fever.")
        if max(scores, key=scores.get) != "entailment":
            raise PipelineError(ErrorClass.POLICY_LOAD_FAILURE, "NLI model failed its identical-sentence check")


def _probabilities(values):
    if all(0.0 <= v <= 1.0 for v in values) and abs(sum(values) - 1.0) < 1e-3:
        return values  # already probabilities
    peak = max(values)
    exps = [math.exp(v - peak) for v in values]
    total = sum(exps)
    return [e / total for e in exps]
