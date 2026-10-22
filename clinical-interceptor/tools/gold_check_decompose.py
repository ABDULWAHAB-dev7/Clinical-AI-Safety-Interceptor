"""Gold check for the decomposer (PLAN 3.3): splits the case-0001 note and compares the claims with the gold propositions.

    python tools/gold_check_decompose.py            # exit 0 only if every gold claim has a claim on the same span

Per gold proposition it reports whether the decomposer produced a claim on the same span with the same text (exact),
on the same span with different text (same_span: the gold file writes a normalised sentence, the rule-based
decomposer keeps the note's own words), on an overlapping span (overlap), or nothing (missing). It also lists claims
the decomposer made that no gold proposition accounts for (extra).

Expected today: it does not pass. The decomposer finds the note's own sentences, the gold file was written by hand with
rewritten sentences, so some text and spans differ. The numbers are reported as they are and the decomposer is not
tuned to the gold file. The note is one synthetic case: even a pass would not show the decomposer works on real notes.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from interceptor.core.claims import decompose_claims  # noqa: E402

SEED = ROOT / "seed"


def run_decompose_check(gold_path=None):
    gold = json.loads(Path(gold_path or SEED / "gold" / "case-0001.gold.json").read_text(encoding="utf-8"))
    text = (SEED / "notes" / Path(gold["note_file"]).name).read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    claims, warnings = decompose_claims(text, sha, note_id="gold-check", include_entities=False)

    exact, same_span, partial, missing = [], [], [], []
    used = set()
    for proposition in gold["propositions"]:
        span = (proposition["char_start"], proposition["char_end"])
        hit = next((i for i, c in enumerate(claims) if (c["char_start"], c["char_end"]) == span), None)
        if hit is not None:
            (exact if claims[hit]["claim_text"] == proposition["claim_text"] else same_span).append(
                proposition["claim_id"])
            used.add(hit)
            continue
        near = next((i for i, c in enumerate(claims) if c["char_start"] < span[1] and span[0] < c["char_end"]), None)
        if near is not None:
            partial.append((proposition["claim_id"], span, (claims[near]["char_start"], claims[near]["char_end"])))
            used.add(near)
        else:
            missing.append(proposition["claim_id"])
    extra = [(c["char_start"], c["char_end"]) for i, c in enumerate(claims) if i not in used]
    return {"case_id": gold["case_id"], "gold": len(gold["propositions"]), "predicted": len(claims), "exact": exact,
            "same_span": same_span, "partial": partial, "missing": missing, "extra": extra, "skipped": len(warnings)}


def main():
    result = run_decompose_check()
    print(f"case {result['case_id']}: {result['gold']} gold claims, {result['predicted']} predicted, "
          f"{result['skipped']} skipped")
    print(f"  same span and text: {len(result['exact'])}")
    print(f"  same span, different text: {len(result['same_span'])}")
    for claim_id, want, got in result["partial"]:
        print(f"  overlap only: {claim_id} gold {want} predicted {got}")
    for claim_id in result["missing"]:
        print(f"  missing: {claim_id}")
    for span in result["extra"]:
        print(f"  extra claim at {span} (no gold proposition)")
    ok = len(result["exact"]) + len(result["same_span"]) == result["gold"] and not result["extra"]
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
