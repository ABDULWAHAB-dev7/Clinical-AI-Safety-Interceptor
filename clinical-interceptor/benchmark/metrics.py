"""Metrics over one run's records.jsonl (PLAN 7.4) and the proposed gates (PLAN 7.5).

    python -m benchmark.metrics --run benchmark/results/<run_id>

Pure functions except for the command line, which writes metrics.json into the run folder.

A gold statement is matched to the smallest predicted claim whose span contains it. Each predicted claim is assumed to
match at most one gold statement, which holds for the v1 corpus (one statement per line). "Exact" means the spans are
equal. Every rate is computed from integer counts, so the bootstrap can resample notes and recompute.
"""

import argparse
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

FLAGGED = ("WARNING", "CRITICAL")
NLI_LABELS = ("ENTAILMENT", "NEUTRAL", "CONTRADICTION")
BOOTSTRAP_SAMPLES = 1000  # PLAN 7.4 step 2
BOOTSTRAP_SEED = 0

# PLAN 7.5 gates. The table in the plan is garbled in places, so only the gates that read clearly are enforced.
# Anything else is reported but not enforced until it is reconciled with PRD.md.
GATES = {
    "BG2": ("critical_recall", ">=", 0.80, "CRITICAL recall (test split)"),
    "BG3": ("clean_note_flag_rate", "<=", 0.05, "clean-note false-positive rate (test split); reading of the table"),
}


def load_records(path):
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def pair_claims(gold, predicted):
    """Returns (pairs, matched_claim_ids). pairs holds (gold_statement, predicted_claim or None)."""
    pairs, used = [], set()
    for statement in gold:
        containing = [p for p in predicted
                      if p["char_start"] <= statement["char_start"] and statement["char_end"] <= p["char_end"]]
        best = min(containing, key=lambda p: p["char_end"] - p["char_start"], default=None)
        pairs.append((statement, best))
        if best is not None:
            used.add(best["claim_id"])
    return pairs, used


def note_counts(record):
    """Integer counts for one note. Summing these over notes gives every metric in `derive`."""
    counts = Counter()
    label = record["label"]
    counts["notes"] += 1
    counts[f"notes_{label}"] += 1
    if record.get("error"):
        counts["notes_error"] += 1
        counts[f"notes_error_{label}"] += 1

    categories = {e["claim_id"]: e["category"] for e in record.get("events", [])}
    pairs, used = pair_claims(record["gold"], record["predicted"])

    for predicted in record["predicted"]:
        if predicted["risk_severity"] in FLAGGED:
            counts["pred_flag"] += 1
        if predicted["risk_severity"] == "CRITICAL":
            counts["pred_crit"] += 1
    note_flagged = any(p["risk_severity"] in FLAGGED for p in record["predicted"])
    if label == "clean" and note_flagged:
        counts["clean_flagged"] += 1

    gold_has_critical = False
    for statement, predicted in pairs:
        gold_flag = statement["expected_risk"] in FLAGGED
        gold_crit = statement["expected_risk"] == "CRITICAL"
        gold_has_critical = gold_has_critical or gold_crit
        counts["gold"] += 1
        counts["gold_flag"] += gold_flag
        counts["gold_crit"] += gold_crit
        if predicted is None:
            continue
        counts["matched"] += 1
        pred_flag = predicted["risk_severity"] in FLAGGED
        pred_crit = predicted["risk_severity"] == "CRITICAL"
        counts["flag_tp"] += gold_flag and pred_flag
        counts["crit_tp"] += gold_crit and pred_crit
        counts[f"sev|{statement['expected_risk']}|{predicted['risk_severity']}"] += 1
        counts["nli_ok"] += statement["expected_nli"] == predicted["nli_class"]
        counts[f"nli|{statement['expected_nli']}|{predicted['nli_class']}"] += 1
        counts["rule_ok"] += statement["expected_risk_rule"] == predicted["risk_rule"]
        counts["ref_ok"] += statement["expected_ref"] == predicted["matched_fhir_ref"]
        counts["exact"] += (predicted["char_start"], predicted["char_end"]) == (
            statement["char_start"], statement["char_end"])
        if gold_flag and pred_flag:
            counts["category_total"] += 1
            counts["category_ok"] += categories.get(predicted["claim_id"]) == statement["expected_category"]

    if record.get("error") is None and record.get("pipeline_status") is not None:
        counts["decision_total"] += 1
        counts["decision_ok"] += (record["pipeline_status"] == "BLOCKED") == gold_has_critical
    return counts


def aggregate(per_note):
    total = Counter()
    for counts in per_note:
        total.update(counts)
    return total


def _ratio(numerator, denominator):
    return None if not denominator else numerator / denominator


def _macro_f1(counts):
    scores = []
    for label in NLI_LABELS:
        tp = counts[f"nli|{label}|{label}"]
        predicted = sum(v for k, v in counts.items() if k.startswith("nli|") and k.split("|")[2] == label)
        actual = sum(v for k, v in counts.items() if k.startswith("nli|") and k.split("|")[1] == label)
        precision, recall = _ratio(tp, predicted), _ratio(tp, actual)
        if precision is None or recall is None or precision + recall == 0:
            scores.append(0.0)
        else:
            scores.append(2 * precision * recall / (precision + recall))
    return sum(scores) / len(scores)


def derive(counts):
    """Every reported rate, from counts. A rate whose denominator is zero is None."""
    matched = counts["matched"]
    return {
        "critical_recall": _ratio(counts["crit_tp"], counts["gold_crit"]),
        "critical_precision": _ratio(counts["crit_tp"], counts["pred_crit"]),
        "flag_recall": _ratio(counts["flag_tp"], counts["gold_flag"]),
        "flag_precision": _ratio(counts["flag_tp"], counts["pred_flag"]),
        "clean_note_flag_rate": _ratio(counts["clean_flagged"], counts["notes_clean"]),
        "note_error_rate": _ratio(counts["notes_error"], counts["notes"]),
        "note_decision_accuracy": _ratio(counts["decision_ok"], counts["decision_total"]),
        "nli_accuracy": _ratio(counts["nli_ok"], matched),
        "nli_macro_f1": _macro_f1(counts) if matched else None,
        "rule_accuracy": _ratio(counts["rule_ok"], matched),
        "fhir_ref_accuracy": _ratio(counts["ref_ok"], matched),
        "category_accuracy": _ratio(counts["category_ok"], counts["category_total"]),
        "offset_containment_rate": _ratio(matched, counts["gold"]),
        "offset_exact_rate": _ratio(counts["exact"], counts["gold"]),
        "counts": dict(sorted(counts.items())),
    }


def bootstrap(per_note, names, samples=BOOTSTRAP_SAMPLES, seed=BOOTSTRAP_SEED):
    """Percentile 95% intervals from resampling notes with replacement. Returns {name: [low, high] or None}."""
    rng = random.Random(seed)
    draws = {name: [] for name in names}
    for _ in range(samples):
        chosen = [per_note[rng.randrange(len(per_note))] for _ in per_note]
        values = derive(aggregate(chosen))
        for name in names:
            if values[name] is not None:
                draws[name].append(values[name])
    intervals = {}
    for name in names:
        ordered = sorted(draws[name])
        if not ordered:
            intervals[name] = None
            continue
        intervals[name] = [_percentile(ordered, 0.025), _percentile(ordered, 0.975)]
    return intervals


def _percentile(ordered, fraction):
    """Nearest-rank percentile of an already sorted list."""
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def latency(records):
    stages = {}
    for record in records:
        for stage, ms in record.get("latency_ms", {}).items():
            stages.setdefault(stage, []).append(ms)
    return {
        stage: {"p50_ms": _percentile(sorted(values), 0.50), "p95_ms": _percentile(sorted(values), 0.95),
                "n": len(values)}
        for stage, values in sorted(stages.items())
    }


def gate_status(values):
    """Each gate as {metric, value, threshold, status}. status is pass, fail, or not_evaluable."""
    results = {}
    for gate, (metric, op, threshold, description) in GATES.items():
        value = values.get(metric)
        if value is None:
            status = "not_evaluable"
        elif op == ">=":
            status = "pass" if value >= threshold else "fail"
        else:
            status = "pass" if value <= threshold else "fail"
        results[gate] = {"description": description, "metric": metric, "value": value,
                         "threshold": f"{op} {threshold}", "status": status}
    results["BG1"] = {"description": "NLI macro-F1 >= 0.95", "metric": "nli_macro_f1",
                      "value": values.get("nli_macro_f1"), "threshold": ">= 0.95",
                      "status": "not_evaluable",
                      "note": "no claim in this corpus needs the NLI model, so NLI is not measured"}
    return results


def evaluate(records):
    """The full report for one run: rates, bootstrap intervals, gates and latency."""
    per_note = [note_counts(r) for r in records]
    values = derive(aggregate(per_note))
    intervals = bootstrap(per_note, ["critical_recall", "critical_precision", "flag_recall", "clean_note_flag_rate"])
    return {
        "notes": len(records),
        "splits": sorted({r.get("split", "?") for r in records}),
        "rates": {k: v for k, v in values.items() if k != "counts"},
        "counts": values["counts"],
        "bootstrap_95": intervals,
        "gates": gate_status(values),
        "latency": latency(records),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, help="a folder under benchmark/results containing records.jsonl")
    args = parser.parse_args(argv)
    run = Path(args.run)
    report = evaluate(load_records(run / "records.jsonl"))
    (run / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['notes']} notes, splits {report['splits']}")
    for name, value in report["rates"].items():
        text = "n/a" if value is None else f"{value:.3f}"
        print(f"  {name:24s} {text}")
    for gate, result in report["gates"].items():
        print(f"  {gate} {result['status']:14s} {result['description']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
