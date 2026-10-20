"""Checks a generated corpus against its own rules (PLAN 7.2).

    python -m benchmark.generator.validate --corpus benchmark/corpus/v1

Exit status 0 only if every check passes. Checks: every gold offset matches the note text; every expected_ref exists
in the patient's bundle or is null; every (nli, risk, rule) triple is one the answer key defines; clean notes hold only
grounded statements; hallucinated notes hold 1 to 3 errors; splits cover every note exactly once; no identifier looks
like a real MRN.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from benchmark.generator.generate import ALLOWED_LABELS, MAX_INJECTIONS

MRN_LIKE = re.compile(r"\b\d{6,}\b|MRN[\s:#]*\d")
SYNTHETIC_MRN = re.compile(r"^SYNTH-MRN-\d{4}$")


def _bundle_refs(bundle):
    refs = set()
    for entry in bundle["entry"]:
        resource = entry["resource"]
        refs.add(f"{resource['resourceType']}/{resource['id']}")
    return refs


def validate(corpus):
    """Returns a list of human-readable errors. An empty list means the corpus passes."""
    corpus = Path(corpus)
    errors = []
    splits = json.loads((corpus / "splits.json").read_text(encoding="utf-8"))
    gold_files = sorted((corpus / "gold").glob("*.gold.json"))
    gold_ids = {p.name[: -len(".gold.json")] for p in gold_files}

    listed = splits["dev"] + splits["test"]
    if len(listed) != len(set(listed)):
        errors.append("splits: a note appears in both splits or twice")
    if set(listed) != gold_ids:
        errors.append(f"splits: {len(set(listed) ^ gold_ids)} note id(s) are missing from splits or have no gold")

    bundles = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (corpus / "patients").glob("*.json")}
    for context, bundle in bundles.items():
        for entry in bundle["entry"]:
            resource = entry["resource"]
            if resource["resourceType"] == "Patient":
                for identifier in resource.get("identifier", []):
                    if not SYNTHETIC_MRN.match(identifier["value"]):
                        errors.append(f"patients/{context}.json: identifier {identifier['value']!r} is not synthetic")

    for path in gold_files:
        gold = json.loads(path.read_text(encoding="utf-8"))
        note_id = gold["note_id"]
        text_path = corpus / "notes" / f"{note_id}.txt"
        if not text_path.exists():
            errors.append(f"{note_id}: note file is missing")
            continue
        data = text_path.read_bytes()
        text = data.decode("utf-8")
        if gold["note_sha256"] != hashlib.sha256(data).hexdigest():
            errors.append(f"{note_id}: note_sha256 does not match the note file")
        if gold["char_length"] != len(text):
            errors.append(f"{note_id}: char_length {gold['char_length']} != {len(text)}")
        if MRN_LIKE.search(text):
            errors.append(f"{note_id}: note text contains a number that looks like an MRN")

        bundle = bundles.get(gold["patient_context"])
        refs = _bundle_refs(bundle) if bundle else set()
        injected = 0
        for prop in gold["propositions"]:
            span = text[prop["char_start"]:prop["char_end"]]
            if span != prop["surface_text"]:
                errors.append(f"{note_id}: offsets {prop['char_start']}-{prop['char_end']} do not match the note")
            if prop["expected_ref"] is not None and prop["expected_ref"] not in refs:
                errors.append(f"{note_id}: expected_ref {prop['expected_ref']} is not in the patient bundle")
            triple = (prop["expected_nli"], prop["expected_risk"], prop["expected_risk_rule"])
            if triple not in ALLOWED_LABELS:
                errors.append(f"{note_id}: label {triple} is not in the answer key")
            if gold["label"] == "clean" and (prop["kind"] != "grounded" or prop["expected_risk"] != "SAFE"):
                errors.append(f"{note_id}: clean note holds a non-grounded statement")
            if prop["kind"] != "grounded":
                injected += 1
        if gold["label"] == "halluc" and not 1 <= injected <= MAX_INJECTIONS:
            errors.append(f"{note_id}: hallucinated note has {injected} injections, expected 1 to {MAX_INJECTIONS}")
        if gold["label"] == "clean" and injected:
            errors.append(f"{note_id}: clean note has injected errors")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", default="benchmark/corpus/v1")
    args = parser.parse_args(argv)
    errors = validate(args.corpus)
    for error in errors[:50]:
        print("ERROR", error)
    if len(errors) > 50:
        print(f"... and {len(errors) - 50} more")
    print("corpus valid" if not errors else f"{len(errors)} error(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
