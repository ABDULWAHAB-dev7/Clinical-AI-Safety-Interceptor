"""Seed acceptance checks S1-S5 (DATABASE.md 4.5).

    python tools/seed_check.py            # exit 0 when every check passes

S1  every FHIR resource validates against the ingestion models
S2  every gold proposition validates against AtomicProposition
S3  every expected_ref resolves to a resource in seed/fhir, or is null
S4  note[char_start:char_end] equals the surface text for every entity; the sentence matches the note
S5  expected_risk and expected_risk_rule match the severity policy, given the gold facts
"""

import hashlib
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from interceptor.core.contracts import AtomicProposition, CodeSystem, MedicalEntity, NLIClass  # noqa: E402
from interceptor.core.severity import ClaimFacts, classify_risk  # noqa: E402
from interceptor.core.terminology import entity_kind, matches_allergy  # noqa: E402
from interceptor.fhir.ingest import ingest, load_ndjson  # noqa: E402

SEED = ROOT / "seed"
NOTE_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")  # fixed, so the seed is reproducible


def run_checks(gold_path=None):
    failures = []
    note_path = SEED / "notes" / "case-0001.txt"
    gold_path = gold_path or SEED / "gold" / "case-0001.gold.json"
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    raw_bytes = note_path.read_bytes()
    note = raw_bytes.decode("utf-8")

    if hashlib.sha256(raw_bytes).hexdigest() != gold["note_sha256"]:
        failures.append("note_sha256 does not match seed/notes/case-0001.txt")

    ingested = ingest(load_ndjson(SEED / "fhir"))
    # S1
    for declared, label, reason in ingested.rejected:
        failures.append(f"S1 {declared} {label}: {reason}")
    fhir_keys = set(ingested.resources)

    allergy_codes = [c.code for (t, _), r in ingested.resources.items() if t == "AllergyIntolerance"
                     for c in r.code.coding]
    active_condition_codes = {
        c.code
        for (t, _), r in ingested.resources.items() if t == "Condition"
        for c in r.code.coding
        if r.clinicalStatus and any(x.code == "active" for x in r.clinicalStatus.coding)
    }

    for p in gold["propositions"]:
        cid = p["claim_id"]
        entities = []
        try:
            entities = [MedicalEntity(**e) for e in p["entities"]]
            AtomicProposition(
                claim_id=cid,
                note_id=NOTE_ID,
                source_sentence=p["source_sentence"],
                source_sentence_start=p["source_sentence_start"],
                source_sentence_end=p["source_sentence_end"],
                claim_text=p["claim_text"],
                char_start=p["char_start"],
                char_end=p["char_end"],
                entities=entities,
            )
        except (ValidationError, ValueError) as exc:
            failures.append(f"S2 {cid}: {type(exc).__name__}")
            continue

        # S4: offsets must reproduce the text exactly
        if note[p["source_sentence_start"]:p["source_sentence_end"]] != p["source_sentence"]:
            failures.append(f"S4 {cid}: source_sentence does not match note offsets")
        for e in entities:
            if note[e.char_start:e.char_end] != e.surface_text:
                failures.append(f"S4 {cid}: entity surface {e.surface_text!r} does not match note offsets")

        # S3
        ref = p["expected_ref"]
        if ref is not None:
            rtype, rid = ref.split("/", 1)
            if (rtype, rid) not in fhir_keys:
                failures.append(f"S3 {cid}: {ref} is not in seed/fhir")

        # S5
        rx_codes = [e.code for e in entities if e.system is CodeSystem.RXNORM]
        kinds = {entity_kind(e.system, e.code, active_condition_codes) for e in entities}
        facts = ClaimFacts(
            nli_class=NLIClass(p["expected_nli"]),
            confidence=p["confidence"],
            is_clinical=p["is_clinical"],
            is_medication_claim=bool(rx_codes),
            matches_allergy=matches_allergy(rx_codes, allergy_codes),
            entity_kinds=frozenset(k for k in kinds if k),
        )
        severity, rule = classify_risk(facts)
        if severity.value != p["expected_risk"] or rule != p["expected_risk_rule"]:
            failures.append(
                f"S5 {cid}: policy gives {severity.value}/{rule}, gold says "
                f"{p['expected_risk']}/{p['expected_risk_rule']}"
            )
    return failures, len(gold["propositions"]), len(ingested.resources)


def main():
    failures, propositions, resources = run_checks()
    print(f"resources ingested: {resources}; propositions checked: {propositions}")
    for failure in failures:
        print("FAIL", failure)
    print("PASS" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
