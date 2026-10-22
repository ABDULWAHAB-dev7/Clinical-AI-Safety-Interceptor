"""Fetch the approved NLI checkpoint, exactly as the lock file describes it.

    python tools/fetch_nli_model.py --out models/nli/cross-encoder-nli-deberta-v3-base

The lock file is the source of truth: it names the repository, the revision and every file with its SHA-256.
This tool fetches exactly those files, at that revision, into a clean directory and then checks them against the
lock. A file that does not match stops the tool.

Only the files in the lock are fetched, so this is about 750 MB rather than the 6 GB of the whole repository:
the exported ONNX graphs and the second copy of the weights in pytorch_model.bin are not used by the service, and
pinning files nobody loads would only make the checkpoint harder to obtain.

The revision is pinned, so the download is the same bytes every time and the lock means the same thing on every
host.
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCK = ROOT / "models" / "nli" / "MODEL.lock"
sys.path.insert(0, str(ROOT / "src"))

from interceptor.nli.scorer import file_digests  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="the directory to fetch the checkpoint into")
    parser.add_argument("--lock", default=str(LOCK))
    args = parser.parse_args()

    lock = json.loads(pathlib.Path(args.lock).read_text(encoding="utf-8"))
    manifest = lock.get("files")
    if not manifest:
        print("The lock file has no file manifest, so there is nothing to fetch.", file=sys.stderr)
        return 1
    repo_id = lock.get("model")
    revision = lock.get("revision")
    if not repo_id or not revision:
        print("The lock file must name the repository and the revision.", file=sys.stderr)
        return 1

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from huggingface_hub import hf_hub_download

    for name in sorted(manifest):
        target = out / name
        if target.is_file():
            print(f"  {name}: already there")
            continue
        print(f"  {name}: fetching")
        fetched = hf_hub_download(repo_id=repo_id, filename=name, revision=revision, local_dir=str(out))
        if pathlib.Path(fetched).resolve() != target.resolve():
            pathlib.Path(fetched).replace(target)

    digests = file_digests(out, list(manifest))
    for name, expected in sorted(manifest.items()):
        if digests[name] != expected:
            print(f"{name} does not match the lock file", file=sys.stderr)
            return 1
    print(f"The checkpoint matches the lock file: {len(manifest)} files verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
