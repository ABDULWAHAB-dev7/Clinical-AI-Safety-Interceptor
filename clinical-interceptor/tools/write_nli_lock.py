"""Regenerate nli_service/MODEL.lock from an approved checkpoint directory.

    python tools/write_nli_lock.py --model <dir> --revision <revision>

The lock names the repository, the revision, and every file that makes up the model with its SHA-256. Only the
files the loader actually reads are listed: the weights, the tokenizer and the config. The exported ONNX graphs,
the second copy of the weights in pytorch_model.bin, the README and the download tool's bookkeeping are not pinned,
because the service never loads them and pinning them would make the checkpoint 6 GB instead of 750 MB.

Run this only when the model itself is re-approved. The lock is the approved artefact, so changing it is a
governance decision, not a build step.
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nli_service.scorer import file_digests  # noqa: E402

LOCK = ROOT / "nli_service" / "MODEL.lock"

# The files that make up the model, in the order the loader reads them.
MODEL_FILES = [
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "spm.model",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True, help="the commit the checkpoint was taken from")
    parser.add_argument("--repo", default="cross-encoder/nli-deberta-v3-base")
    parser.add_argument("--license", dest="licence", default="apache-2.0 (model card)")
    parser.add_argument("--out", default=str(LOCK))
    args = parser.parse_args()

    model = pathlib.Path(args.model)
    digests = file_digests(model, MODEL_FILES)
    lock = {
        "model": args.repo,
        "revision": args.revision,
        "license": args.licence,
        "hash_algorithm": "sha256 of each file's content; the files are listed with their digests",
        "files": digests,
        "note": "Only the files the service loads are pinned: the weights, the tokenizer and the config. The "
                "exported ONNX graphs, pytorch_model.bin, the README and the download tool's .cache directory are "
                "not part of the model the service runs, and the download tool's bookkeeping holds the time of the "
                "download, so pinning it would make the lock unreproducible.",
    }
    pathlib.Path(args.out).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(lock, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
