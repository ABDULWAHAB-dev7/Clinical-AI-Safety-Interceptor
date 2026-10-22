"""Gold check for the verifier (PLAN 4.6): runs the case-0001 gold claims through core.verify and compares the output
with the expected_* fields in the gold file.

    python tools/gold_check_verify.py            # exit 0 only if every claim matches

This checks the verifier on gold claims and gold entities. It does not yet run decomposition first (the plan's full
decompose-then-verify check), because the rule-based decomposer does not reproduce the gold claim spans.

A claim that needs the NLI scorer and has none is reported as "needs NLI". It is never given a default result.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from interceptor.core.contracts import CodeSystem  # noqa: E402
from interceptor.core.entities import Entity  # noqa: E402
from interceptor.core.knowledge import load_knowledge  # noqa: E402
from interceptor.core.verify import VerificationError, verify_claim  # noqa: E402
from interceptor.fhir.ingest import ingest, load_ndjson  # noqa: E402
from interceptor.fhir.triples import triples_from_ingest  # noqa: E402

SEED = ROOT / "seed"
FIELDS = (
    ("nli_class", "expected_nli"),
    ("matched_fhir_ref", "expected_ref"),
    ("risk_severity", "expected_risk"),
    ("risk_rule", "expected_risk_rule"),
)


def run_gold_check(gold_path=None, nli_model=None):
    gold = json.loads(Path(gold_path or SEED / "gold" / "case-0001.gold.json").read_text(encoding="utf-8"))
    knowledge = load_knowledge()
    triples = triples_from_ingest(ingest(load_ndjson(SEED / "fhir")), knowledge)

    matched = {field: 0 for field, _ in FIELDS}
    needs_nli, mismatches = [], []
    for proposition in gold["propositions"]:
        entities = [
            Entity(CodeSystem(e["system"]), e["code"], e["display"], e["surface_text"], e["char_start"], e["char_end"])
            for e in proposition["entities"]
        ]
        claim = {"claim_id": proposition["claim_id"], "claim_text": proposition["claim_text"], "entities": entities}
        try:
            result, _ = verify_claim(claim, triples, knowledge, "gold-check", nli_model)
        except VerificationError as exc:
            needs_nli.append((proposition["claim_id"], exc.reason))
            continue
        for field, expected in FIELDS:
            if result[field] == proposition[expected]:
                matched[field] += 1
            else:
                mismatches.append((proposition["claim_id"], field, result[field], proposition[expected]))
    return {"total": len(gold["propositions"]), "matched": matched, "needs_nli": needs_nli, "mismatches": mismatches,
            "case_id": gold["case_id"]}


def main():
    report = run_gold_check()
    total = report["total"]
    counts = ", ".join(f"{report['matched'][field]}/{total} {field}" for field, _ in FIELDS)
    print(f"{report['case_id']}: {counts}")
    for claim_id, reason in report["needs_nli"]:
        print(f"  needs NLI: {claim_id}: {reason}")
    for claim_id, field, got, want in report["mismatches"]:
        print(f"  MISMATCH {claim_id} {field}: got {got!r}, gold {want!r}")
    ok = not report["needs_nli"] and not report["mismatches"]
    print("PASS" if ok else "INCOMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
