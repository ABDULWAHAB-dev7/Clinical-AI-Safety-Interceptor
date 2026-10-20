"""Real-note evaluation on public MTSamples transcriptions (not MIMIC-IV; Gate G2 is not needed for these).

    python -m benchmark.realnotes.run_realnotes --notes <folder with note_NN.txt and manifest.json> --out <results folder>

Every note gives two documents: the note as written (clean) and the same note with labelled errors appended (injected).

The record is synthetic, and it is built only from facts the note itself states that the verifier can encode: type 2
diabetes (a Condition) and an HbA1c value (an Observation). It is NOT an independent ground truth, because nobody
reviewed it against a real chart.

Errors are injected only when the record can check them:
  fabricated_history   "History of stroke in 2015."  unsupported by the record (expected WARNING)
  inverted_lab         "HbA1c was X% ..." with X far from the record's value (expected WARNING), when HbA1c exists

Errors that need a record which states the fact are NOT scored: a dosing-frequency error, a beta-lactam cross-reactivity
error, and an allergy denial. None of the 20 notes documents an allergy or a medication the verifier can encode, so the
record cannot contradict them. They are reported as not evaluated.

Scores:
  blocking   document level. A document is caught if it gets a CRITICAL event. Clean documents that get blocked are
             false-positive blocks. Fail-closed errors (the NLI model is missing) are counted separately.
  flagging   claim level. Positive = the injected error's claim. Negative = every other claim, including the unsupported
             native statements. A flag is any WARNING or CRITICAL.

Note text is never written to the results. Only hashes, spans, severities and metrics are.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from benchmark.runner import BENCH_KEY, Client, run_note
from interceptor.core.pseudo import patient_pseudo_id

T2DM_CODE = {"system": "http://snomed.info/sct", "code": "44054006", "display": "Diabetes mellitus type 2"}
HBA1C_DATE = "2026-09-02"
DIABETES_TYPE2 = re.compile(
    r"type\s*(?:2|ii|two)\s*diabet|diabetes\s*(?:mellitus\s*)?type\s*(?:2|ii)|\bT2DM\b|\bDM2\b", re.I
)
HBA1C = re.compile(r"(?:hba1c|a1c|hemoglobin a1c)[^0-9\n%]{0,30}(\d+(?:\.\d+)?)\s*%", re.I)
FLAGGED = ("WARNING", "CRITICAL")


def facts(body):
    """What the note states that the verifier can encode: type 2 diabetes and an HbA1c value."""
    match = HBA1C.search(body)
    return {"t2dm": bool(DIABETES_TYPE2.search(body)), "hba1c": float(match.group(1)) if match else None}


def bundle_for(number, body):
    """A synthetic FHIR R4 record, built from the note's own stated facts only."""
    mrn = f"SYNTH-MRN-RN{number:03d}"
    patient = f"pat-rn{number:03d}"
    known = facts(body)
    entries = [{"resource": {"resourceType": "Patient", "id": patient,
                             "identifier": [{"system": "urn:synthetic:mrn", "value": mrn}]}}]
    if known["t2dm"]:
        entries.append({"resource": {
            "resourceType": "Condition", "id": "cond-t2dm-001",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                                           "code": "active"}]},
            "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                                               "code": "confirmed"}]},
            "code": {"coding": [T2DM_CODE]}, "subject": {"reference": f"Patient/{patient}"},
            "onsetDateTime": "2019-05-02T00:00:00Z"}})
    if known["hba1c"] is not None:
        entries.append({"resource": {
            "resourceType": "Observation", "id": "obs-hba1c-001", "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                      "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4",
                                 "display": "Hemoglobin A1c/Hemoglobin.total in Blood"}]},
            "subject": {"reference": f"Patient/{patient}"}, "effectiveDateTime": f"{HBA1C_DATE}T08:30:00Z",
            "valueQuantity": {"value": known["hba1c"], "unit": "%", "system": "http://unitsofmeasure.org",
                              "code": "%"}}})
    return mrn, {"resourceType": "Bundle", "type": "collection", "entry": entries}


def injections(body):
    """Encodable errors only (see the module docstring)."""
    known = facts(body)
    items = [("fabricated_history", "History of stroke in 2015.", "WARNING")]
    if known["hba1c"] is not None:
        wrong = 6.1 if abs(known["hba1c"] - 6.1) > 1.0 else 9.4  # far from the record's value
        items.append(("inverted_lab", f"HbA1c was {wrong:g}% on {HBA1C_DATE}.", "WARNING"))
    return items


NOT_EVALUATED = [
    ("dosing_frequency", "needs a record that states the medication and its frequency"),
    ("beta_lactam_cross_reactivity", "needs a documented penicillin allergy in the record"),
    ("allergy_denial", "needs a documented allergy in the record"),
]


def build_documents(notes):
    docs = []
    for number, body in notes:
        clean = body.strip()
        docs.append({"doc_id": f"n{number:02d}-clean", "note": number, "label": "clean", "text": clean, "errors": []})
        text, errors = clean, []
        for kind, line, expected in injections(clean):
            start = len(text) + 1
            text = text + "\n" + line
            # the claim span excludes the final period, so the error span does too
            errors.append({"type": kind, "expected": expected, "start": start,
                           "end": start + len(line.rstrip("."))})
        docs.append({"doc_id": f"n{number:02d}-injected", "note": number, "label": "injected", "text": text,
                     "errors": errors})
    return docs


def claims_of(record):
    """Claim spans and severities only, so the results hold no text."""
    return [{"claim_id": c["claim_id"], "char_start": c["char_start"], "char_end": c["char_end"],
             "risk_severity": c["risk_severity"], "risk_rule": c["risk_rule"]} for c in record["predicted"]]


def containing(claims, start, end):
    hits = [c for c in claims if c["char_start"] <= start and end <= c["char_end"]]
    return min(hits, key=lambda c: c["char_end"] - c["char_start"]) if hits else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--notes", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    folder = Path(args.notes)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    notes = []
    for entry in manifest:
        body = (folder / f"note_{entry['id']:02d}.txt").read_text(encoding="utf-8")
        if body.strip():
            notes.append((entry["id"], body))
    urls = {entry["id"]: entry["url"] for entry in manifest}

    client = Client()
    records = []
    for doc in build_documents(notes):
        number = doc["note"]
        body = dict(notes)[number]
        mrn, bundle = bundle_for(number, body)
        record = run_note(client, doc["doc_id"], doc["label"], f"RN{number:03d}", doc["text"], [], bundle,
                          patient_pseudo_id(mrn, BENCH_KEY))
        claims = claims_of(record)
        critical = [e for e in record["events"] if e["risk_severity"] == "CRITICAL"]
        injected_hits = []
        for error in doc["errors"]:
            hit = containing(claims, error["start"], error["end"])
            injected_hits.append({"type": error["type"], "expected": error["expected"],
                                  "severity": hit["risk_severity"] if hit else None,
                                  "rule": hit["risk_rule"] if hit else None})
        records.append({
            "doc_id": doc["doc_id"], "label": doc["label"], "note": number, "source": urls[number],
            "text_sha256": hashlib.sha256(doc["text"].encode("utf-8")).hexdigest(),
            "pipeline_status": record["pipeline_status"], "error": record["error"],
            "critical_events": len(critical), "caught": bool(critical),
            "claims": claims,
            "injections": injected_hits,
            "latency_ms": record["latency_ms"],
            "error_offsets": [[e["start"], e["end"], e["type"]] for e in doc["errors"]],
        })
        sys.stdout.write(f"{doc['doc_id']}: {record['pipeline_status'] or record['error']} "
                         f"critical={len(critical)}\n")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    report = score(records)
    report["not_evaluated"] = [{"type": t, "reason": why} for t, why in NOT_EVALUATED]
    (out / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def score(records):
    clean = [r for r in records if r["label"] == "clean"]
    injected = [r for r in records if r["label"] == "injected"]

    # blocking, document level
    b_tp = sum(r["caught"] for r in injected)
    b_fn = len(injected) - b_tp
    b_fp = sum(r["caught"] for r in clean)
    b_tn = len(clean) - b_fp
    b_precision = _ratio(b_tp, b_tp + b_fp)
    b_recall = _ratio(b_tp, b_tp + b_fn)
    blocking = {"tp": b_tp, "fp": b_fp, "fn": b_fn, "tn": b_tn, "precision": b_precision, "recall": b_recall,
                "f1": (2 * b_precision * b_recall / (b_precision + b_recall)) if b_precision and b_recall else None}

    # flagging, claim level
    positives = set()
    for r in injected:
        for error in r["error_offsets"]:
            positives.add((r["doc_id"], error[0], error[1]))
    f_tp = f_fp = f_fn = f_tn = 0
    for r in records:
        for claim in r["claims"]:
            flagged = claim["risk_severity"] in FLAGGED
            is_error = any(
                doc_id == r["doc_id"] and claim["char_start"] <= start and end <= claim["char_end"]
                for doc_id, start, end in positives
            )
            if is_error and flagged:
                f_tp += 1
            elif is_error:
                f_fn += 1
            elif flagged:
                f_fp += 1
            else:
                f_tn += 1
    f_precision = _ratio(f_tp, f_tp + f_fp)
    f_recall = _ratio(f_tp, f_tp + f_fn)
    flagging = {"tp": f_tp, "fp": f_fp, "fn": f_fn, "tn": f_tn, "precision": f_precision, "recall": f_recall,
                "f1": (2 * f_precision * f_recall / (f_precision + f_recall)) if f_precision and f_recall else None}

    per_type = {}
    for r in injected:
        for hit in r["injections"]:
            bucket = per_type.setdefault(hit["type"], {"injected": 0, "critical": 0, "warning": 0, "safe": 0,
                                                       "no_claim": 0})
            bucket["injected"] += 1
            key = {"CRITICAL": "critical", "WARNING": "warning", "SAFE": "safe"}.get(hit["severity"], "no_claim")
            bucket[key] += 1

    return {
        "documents": {"clean": len(clean), "injected": len(injected)},
        "blocking_document_level": blocking,
        "flagging_claim_level": flagging,
        "false_positive_block_rate": _ratio(b_fp, len(clean)),
        "fail_closed_documents": {"clean": sum(1 for r in clean if r["error"]),
                                  "injected": sum(1 for r in injected if r["error"])},
        "per_injection_type": per_type,
    }


if __name__ == "__main__":
    main()
