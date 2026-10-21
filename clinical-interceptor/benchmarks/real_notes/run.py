"""Real-note benchmark (Milestone 3.2). Verifies 10 synthetic clinical notes against their own FHIR charts and scores the
verifier against the errors seeded into the notes.

    python benchmarks/real_notes/run.py          # writes results/results.json and docs/VALIDATION_REPORT.md

The run uses the same core functions as the API: decompose_claims, then verify_claim (with the local NLI model), then the
severity rules. It does not call the HTTP routes, so no database or certificate is needed.

Per claim:
  inside   the claim starts inside a seeded sentence (a seeded error)
  flag     severity WARNING or CRITICAL, or FAIL_CLOSED
  block    severity CRITICAL or FAIL_CLOSED. The API refuses a document with a CRITICAL claim, and a fail-closed claim
           refuses the whole request, so both count as blocked.

Scores (claim level unless stated):
  flagging  precision, recall, F1 over: positive = inside a seeded sentence, predicted = flag
  blocking  precision, recall, F1 over: positive = inside a seeded sentence whose expected severity is CRITICAL,
            predicted = block
  document  a document is blocked if any claim is blocked. Positive = the document holds a CRITICAL seeded error.

Note text, claim text and chart facts are synthetic, written for this benchmark. No real patient data is used.
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interceptor.core.claims import decompose_claims  # noqa: E402
from interceptor.core.contracts import CodeSystem  # noqa: E402
from interceptor.core.entities import Entity  # noqa: E402
from interceptor.core.knowledge import load_knowledge  # noqa: E402
from interceptor.core.verify import VerificationError, verify_claim  # noqa: E402
from interceptor.fhir.ingest import ingest  # noqa: E402
from interceptor.fhir.triples import triples_from_ingest  # noqa: E402
from interceptor.nli.scorer import NLIScorer  # noqa: E402

VERSION = "real-notes-benchmark-1"
MODEL_DIR = ROOT / "models" / "nli" / "cross-encoder-nli-deberta-v3-base"
LOCK_FILE = ROOT / "models" / "nli" / "MODEL.lock"
CASES = HERE / "cases"
RESULTS = HERE / "results" / "results.json"
REPORT = ROOT / "docs" / "VALIDATION_REPORT.md"
KNOWLEDGE = load_knowledge()
FLAG = {"WARNING", "CRITICAL", "FAIL_CLOSED"}
BLOCK = {"CRITICAL", "FAIL_CLOSED"}


def load_case(folder):
    note = (folder / "note.txt").read_text(encoding="utf-8")
    gold = json.loads((folder / "gold.json").read_text(encoding="utf-8"))
    bundle = json.loads((folder / "bundle.json").read_text(encoding="utf-8"))
    seeded = []
    for item in gold["seeded"]:
        count = note.count(item["sentence"])
        if count != 1:
            raise SystemExit(f"{folder.name}: seeded sentence must appear exactly once, found {count}: {item['sentence']!r}")
        start = note.index(item["sentence"])
        seeded.append({**item, "start": start, "end": start + len(item["sentence"])})
    raw = [(e["resource"]["resourceType"], f"{folder.name}:{i}", e["resource"])
           for i, e in enumerate(bundle["entry"], start=1)]
    ingested = ingest(raw)
    if ingested.rejected:
        raise SystemExit(f"{folder.name}: chart resources rejected: {ingested.rejected}")
    return note, seeded, triples_from_ingest(ingested, KNOWLEDGE)


def to_entity(e):
    return Entity(CodeSystem(e["system"]), e["code"], e["display"], e["surface_text"], e["char_start"], e["char_end"])


def verify_case(folder, scorer):
    note, seeded, triples = load_case(folder)
    claims, warnings = decompose_claims(note, hashlib.sha256(note.encode("utf-8")).hexdigest(), folder.name,
                                        knowledge=KNOWLEDGE)
    rows = []
    for claim in claims:
        request = {"claim_id": claim["claim_id"], "claim_text": claim["claim_text"],
                   "char_start": claim["char_start"], "entities": [to_entity(e) for e in claim["entities"]]}
        try:
            result, _ = verify_claim(request, triples, KNOWLEDGE, VERSION, scorer)
            severity, rule, nli = result["risk_severity"], result["risk_rule"], result["nli_class"]
        except VerificationError as exc:
            severity, rule, nli = "FAIL_CLOSED", exc.reason, None
        owner = next((s for s in seeded if s["start"] <= claim["char_start"] < s["end"]), None)
        rows.append({
            "claim_id": claim["claim_id"], "claim_text": claim["claim_text"], "char_start": claim["char_start"],
            "severity": severity, "rule": rule, "nli_class": nli,
            "medication": any(e["system"] == CodeSystem.RXNORM.value for e in claim["entities"]),
            "seeded": owner["kind"] if owner else None,
            "expected": owner["expected"] if owner else None,
            "seeded_span": [owner["start"], owner["end"]] if owner else None,
        })
    return {"case": folder.name, "chart": json.loads((folder / "gold.json").read_text(encoding="utf-8"))["chart"],
            "note_type": json.loads((folder / "gold.json").read_text(encoding="utf-8"))["note_type"],
            "claim_count": len(claims), "skipped_sentences": len(warnings),
            "seeded": [{k: s[k] for k in ("kind", "expected", "sentence", "chart_fact", "start", "end")} for s in seeded],
            "claims": rows}


def prf(tp, fp, fn):
    denominator = 2 * tp + fp + fn
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / denominator if denominator else None}


def score(cases):
    claims = [c for case in cases for c in case["claims"]]
    inside = [c for c in claims if c["seeded"]]
    outside = [c for c in claims if not c["seeded"]]
    block_gold = [c for c in inside if c["expected"] == "CRITICAL"]
    flag_tp = sum(c["severity"] in FLAG for c in inside)
    flag_fp = sum(c["severity"] in FLAG for c in outside)
    block_tp = sum(c["severity"] in BLOCK for c in block_gold)
    block_fp = sum(c["severity"] in BLOCK for c in claims if c not in block_gold)
    doc_gold = [any(s["expected"] == "CRITICAL" for s in case["seeded"]) for case in cases]
    doc_pred = [any(c["severity"] in BLOCK for c in case["claims"]) for case in cases]
    doc_tp = sum(g and p for g, p in zip(doc_gold, doc_pred))
    doc_fp = sum((not g) and p for g, p in zip(doc_gold, doc_pred))
    doc_fn = sum(g and (not p) for g, p in zip(doc_gold, doc_pred))
    seeded_rows = []
    for case in cases:
        for s in case["seeded"]:
            mine = [c for c in case["claims"] if c["seeded_span"] == [s["start"], s["end"]]]
            seeded_rows.append({"case": case["case"], "kind": s["kind"], "expected": s["expected"],
                                "sentence": s["sentence"], "claims_in_span": len(mine),
                                "severities": [c["severity"] for c in mine],
                                "caught_flag": any(c["severity"] in FLAG for c in mine),
                                "caught_block": any(c["severity"] in BLOCK for c in mine)})
    clean_medication = [c for c in outside if c["medication"]]
    return {
        "claims_total": len(claims), "claims_inside_seeded": len(inside), "claims_clean": len(outside),
        "flagging": prf(flag_tp, flag_fp, len(inside) - flag_tp),
        "blocking_claim": prf(block_tp, block_fp, len(block_gold) - block_tp),
        "blocking_document": prf(doc_tp, doc_fp, doc_fn),
        "clean_flagged": sum(c["severity"] in FLAG for c in outside),
        "clean_flagged_medication": sum(c["severity"] in FLAG for c in clean_medication),
        "clean_medication_total": len(clean_medication),
        "clean_blocked": sum(c["severity"] in BLOCK for c in outside),
        "seeded": seeded_rows,
        "documents": len(cases), "documents_expected_block": sum(doc_gold),
        "documents_blocked": sum(doc_pred),
        "fail_closed": sum(c["severity"] == "FAIL_CLOSED" for c in claims),
    }


def fmt(value):
    return "n/a" if value is None else f"{value:.2f}"


def write_report(summary, cases):
    s = summary
    lines = [
        "# Validation report: real-note benchmark (Milestone 3.2)",
        "",
        "Generated by `benchmarks/real_notes/run.py`. Every number below is computed from `benchmarks/real_notes/results/results.json`.",
        "",
        "> Synthetic data only. Not validated for clinical use, not a medical device, not for patient care.",
        "",
        "## What was tested",
        "",
        f"- {s['documents']} clinical notes in SOAP, discharge, clinic, urgent-care and consultation styles, written for this benchmark.",
        "- Each note has its own synthetic FHIR R4 chart (`bundle.json`), written before the note and not derived from it.",
        f"- {sum(len(c['seeded']) for c in cases)} errors were seeded into {sum(1 for c in cases if c['seeded'])} notes. Cases 01 to 03 are clean.",
        f"- {s['claims_total']} claims were decomposed and verified with the local NLI model (`cross-encoder/nli-deberta-v3-base`, lock checked).",
        "",
        "## Results",
        "",
        "| Measure | Positives | TP | FP | FN | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, key, positives in (
        ("Flagging, claim level (WARNING or CRITICAL)", "flagging", s["claims_inside_seeded"]),
        ("Blocking, claim level (CRITICAL)", "blocking_claim", None),
        ("Blocking, document level", "blocking_document", s["documents_expected_block"]),
    ):
        m = s[key]
        pos = positives if positives is not None else m["tp"] + m["fn"]
        lines.append(f"| {label} | {pos} | {m['tp']} | {m['fp']} | {m['fn']} | {fmt(m['precision'])} | {fmt(m['recall'])} | {fmt(m['f1'])} |")
    lines += [
        "",
        "## Seeded errors",
        "",
        "| Case | Kind | Expected | Caught as WARNING or CRITICAL | Caught as CRITICAL | Severities of the claims in the span |",
        "|---|---|---|---|---|---|",
    ]
    for row in s["seeded"]:
        lines.append(f"| {row['case']} | {row['kind']} | {row['expected']} | {'yes' if row['caught_flag'] else 'no'} | "
                     f"{'yes' if row['caught_block'] else 'no'} | {', '.join(row['severities']) or 'none'} |")
    lines += [
        "",
        "## Clean statements",
        "",
        f"- Clean claims (outside every seeded sentence): {s['claims_clean']}.",
        f"- Clean claims flagged WARNING or CRITICAL: {s['clean_flagged']}, of which {s['clean_flagged_medication']} of {s['clean_medication_total']} are medication statements.",
        f"- Clean claims blocked: {s['clean_blocked']}.",
        f"- Documents blocked: {s['documents_blocked']} of {s['documents']}; {s['documents_expected_block']} were expected to be blocked.",
        f"- Claims that failed closed (verifier error): {s['fail_closed']}.",
        "",
        "Clean claims that were blocked (false blocks):",
        "",
        "| Case | Severity | Rule | NLI class | Claim |",
        "|---|---|---|---|---|",
    ]
    blocked_clean = [(c["case"], r) for c in cases for r in c["claims"] if not r["seeded"] and r["severity"] in BLOCK]
    if blocked_clean:
        lines += [f"| {case} | {r['severity']} | {r['rule']} | {r['nli_class']} | {r['claim_text']} |"
                  for case, r in blocked_clean]
    else:
        lines.append("None.")
    lines += [
        "",
        "## Not evaluated",
        "",
        "- **Penicillin to cephalosporin cross-reactivity.** No verifier rule exists; a cephalosporin in a penicillin-allergic patient is not checked.",
        "- **Laboratory critical values.** Each laboratory sets its own limits, and no single standard was found (see `knowledge/lexicon_seed.csv`). No critical-value rule is applied.",
        "- **Dosing frequency beyond the three configured drugs.** Dose ceilings exist only for amoxicillin and metformin (`knowledge/dose_limits.yaml`).",
        "- **Medication claims generally.** The FHIR subset has no medication facts, so every medication statement is NEUTRAL and is flagged as WARNING.",
        "",
        "## Limits of this evaluation",
        "",
        "- The sample is small: 10 notes and 8 seeded errors. One error changes a recall by about 0.12, so the figures are indicative, not statistically precise.",
        "- The notes and charts were written by the same author, and the seeded errors were chosen to be checkable by the verifier. The benchmark measures the verifier on errors of the kinds it encodes, not on errors in general.",
        "- No note or chart comes from a real patient or from the MTSamples transcriptions. Real-note data needs Gate G2 approval.",
        "- The NLI model is not clinically validated. Its results on clinical statements are not known beyond this benchmark.",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main():
    scorer = NLIScorer(str(MODEL_DIR), lock_path=str(LOCK_FILE))
    scorer.warm_up()
    cases = [verify_case(folder, scorer) for folder in sorted(CASES.iterdir()) if (folder / "gold.json").exists()]
    summary = score(cases)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps({"summary": summary, "cases": cases}, indent=2) + "\n", encoding="utf-8")
    write_report(summary, cases)
    print(json.dumps({"flagging": summary["flagging"], "blocking_claim": summary["blocking_claim"],
                      "blocking_document": summary["blocking_document"],
                      "clean_flagged": summary["clean_flagged"], "claims_total": summary["claims_total"]}, indent=1))


if __name__ == "__main__":
    main()
