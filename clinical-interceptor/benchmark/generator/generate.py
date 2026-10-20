"""Synthetic note generator with error injection (PLAN 7.2). Every name, identifier and value here is invented.

    python -m benchmark.generator.generate --seed 20261019 --n-clean 200 --n-halluc 200 --out benchmark/corpus/v1

Clean notes contain only statements grounded in the patient's record. Hallucinated notes add 1 to 3 injected errors.
Gold labels come from the template table below, written by hand. They are not read back from the verifier, so the
benchmark compares the verifier with an independent answer key.

Standard library only, so the generator runs without the interceptor installed.
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEV_FRACTION = 0.4
MAX_INJECTIONS = 3

ALLERGY_REF = "AllergyIntolerance/allergy-penicillin-001"
CONDITION_REF = "Condition/cond-t2dm-001"
OBS_REF = "Observation/obs-hba1c-001"

# Synthetic patient contexts. `allergy` and `t2dm` say whether that fact is in the record. Every context has the HbA1c
# observation, so the lab templates always apply.
CONTEXTS = {
    "A": {"allergy": True, "t2dm": True, "obs": True},
    "B": {"allergy": False, "t2dm": True, "obs": True},
    "C": {"allergy": True, "t2dm": False, "obs": True},
    "D": {"allergy": False, "t2dm": False, "obs": True},
}

# Each template: (requirement, statement). A requirement of None always applies. "no_allergy" needs the allergy to be
# absent. Gold fields follow the names used in seed/gold/case-0001.gold.json.


def _statement(texts, nli, risk, rule, ref, category, kind):
    """`texts` are paraphrases with the same gold label. Each note uses one of them, chosen by the seeded RNG."""
    return {
        "texts": texts,
        "expected_nli": nli,
        "expected_risk": risk,
        "expected_risk_rule": rule,
        "expected_ref": ref,
        "expected_category": category,
        "kind": kind,
    }


GROUNDED = [
    ("allergy", _statement(("Patient has drug allergies.", "Known drug allergies are documented."),
                           "ENTAILMENT", "SAFE", "entailed", ALLERGY_REF, None, "grounded")),
    ("t2dm", _statement(("Patient has type 2 diabetes.", "History of type 2 diabetes is documented."),
                        "ENTAILMENT", "SAFE", "entailed", CONDITION_REF, None, "grounded")),
    ("obs", _statement(("HbA1c was 8.2% on 2026-09-02.", "Hemoglobin A1c measured 8.2% on 2026-09-02."),
                       "ENTAILMENT", "SAFE", "entailed", OBS_REF, None, "grounded")),
]

INJECTED = [
    ("allergy", _statement(("Patient denies known drug allergies.", "No known drug allergies."),
                           "CONTRADICTION", "CRITICAL", "contradiction_high_risk", ALLERGY_REF,
                           "DRUG_ALLERGY_CONFLICT", "allergy_denial")),
    ("allergy", _statement(("Started amoxicillin 500 mg TID for sinusitis.",
                            "Amoxicillin 500 mg three times daily was prescribed."),
                           "NEUTRAL", "CRITICAL", "drug_allergy_conflict", ALLERGY_REF,
                           "DRUG_ALLERGY_CONFLICT", "drug_allergy_order")),
    ("t2dm", _statement(("No history of type 2 diabetes.", "Type 2 diabetes is not present."),
                        "CONTRADICTION", "CRITICAL", "contradiction_high_risk", CONDITION_REF,
                        "DIAGNOSIS_CONTRADICTION", "diagnosis_contradiction")),
    ("obs", _statement(("HbA1c was 6.1% on 2026-09-02.", "Hemoglobin A1c measured 6.1% on 2026-09-02."),
                       "CONTRADICTION", "WARNING", "contradiction", OBS_REF, "LAB_VALUE_MISMATCH",
                       "lab_value_mismatch")),
    ("no_allergy", _statement(("Started amoxicillin 1500 mg once.", "Amoxicillin 1500 mg was given once."),
                              "CONTRADICTION", "CRITICAL", "contradiction_high_risk", None,
                              "MEDICATION_DOSE_MISMATCH", "dose_above_limit")),
    (None, _statement(("Patient reports headaches.", "Patient reports nausea."), "NEUTRAL", "WARNING",
                      "unsupported_clinical", None, "UNSUPPORTED_CLAIM", "unsupported")),
]

# The only (nli, risk, rule) triples the answer key uses. validate.py rejects anything else.
ALLOWED_LABELS = {(s["expected_nli"], s["expected_risk"], s["expected_risk_rule"])
                  for _, s in GROUNDED + INJECTED}


def applies(requirement, context):
    spec = CONTEXTS[context]
    if requirement is None:
        return True
    if requirement == "no_allergy":
        return not spec["allergy"]
    return spec[requirement]


def patient_bundle(context):
    spec = CONTEXTS[context]
    index = "ABCD".index(context) + 1
    patient_id = f"pat-{context.lower()}"
    resources = [{"resourceType": "Patient", "id": patient_id,
                  "identifier": [{"system": "urn:synthetic:mrn", "value": f"SYNTH-MRN-{index:04d}"}]}]
    if spec["allergy"]:
        resources.append({"resourceType": "AllergyIntolerance", "id": "allergy-penicillin-001",
                          "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/"
                                                                   "allergyintolerance-clinical",
                                                         "code": "active"}]},
                          "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/"
                                                                       "allergyintolerance-verification",
                                                             "code": "confirmed"}]},
                          "type": "allergy", "category": ["medication"], "criticality": "high",
                          "code": {"coding": [{"system": "http://snomed.info/sct", "code": "91936005",
                                               "display": "Allergy to penicillin"}]},
                          "patient": {"reference": f"Patient/{patient_id}"}})
    if spec["t2dm"]:
        resources.append({"resourceType": "Condition", "id": "cond-t2dm-001",
                          "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/"
                                                                   "condition-clinical", "code": "active"}]},
                          "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/"
                                                                       "condition-ver-status", "code": "confirmed"}]},
                          "code": {"coding": [{"system": "http://snomed.info/sct", "code": "44054006",
                                               "display": "Diabetes mellitus type 2"}]},
                          "subject": {"reference": f"Patient/{patient_id}"},
                          "onsetDateTime": "2019-05-02T00:00:00Z"})
    if spec["obs"]:
        resources.append({"resourceType": "Observation", "id": "obs-hba1c-001", "status": "final",
                          "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/"
                                                              "observation-category", "code": "laboratory"}]}],
                          "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4",
                                               "display": "Hemoglobin A1c/Hemoglobin.total in Blood"}]},
                          "subject": {"reference": f"Patient/{patient_id}"},
                          "effectiveDateTime": "2026-09-02T08:30:00Z",
                          "valueQuantity": {"value": 8.2, "unit": "%", "system": "http://unitsofmeasure.org",
                                            "code": "%"}})
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in resources]}


def build_note(statements):
    """Joins one statement per line. A span excludes the final period, as in seed/gold/case-0001.gold.json and the
    decomposer's claim spans."""
    parts, propositions, position = [], [], 0
    for index, statement in enumerate(statements):
        if index:
            parts.append("\n")
            position += 1
        start = position
        parts.append(statement["text"])
        position += len(statement["text"])
        surface = statement["text"].rstrip(".")
        propositions.append({
            "char_start": start,
            "char_end": start + len(surface),
            "surface_text": surface,
            "claim_text": surface,
            "kind": statement["kind"],
            "expected_nli": statement["expected_nli"],
            "expected_risk": statement["expected_risk"],
            "expected_risk_rule": statement["expected_risk_rule"],
            "expected_ref": statement["expected_ref"],
            "expected_category": statement["expected_category"],
            "is_clinical": True,
        })
    return "".join(parts), propositions


def _pick_grounded(rng, context, minimum):
    eligible = [s for requirement, s in GROUNDED if applies(requirement, context)]
    return rng.sample(eligible, rng.randint(minimum, len(eligible)))


def _pick_injected(rng, context):
    eligible = [s for requirement, s in INJECTED if applies(requirement, context)]
    return rng.sample(eligible, rng.randint(1, min(MAX_INJECTIONS, len(eligible))))


def generate_notes(seed, n_clean, n_halluc):
    """Returns note dicts with keys: note_id, label, patient_context, text, propositions. Deterministic for a seed."""
    rng = random.Random(seed)
    notes = []
    for label, count in (("clean", n_clean), ("halluc", n_halluc)):
        for number in range(1, count + 1):
            context = rng.choice(sorted(CONTEXTS))
            # Clean notes have at least one grounded statement. Hallucinated notes may have none beyond the errors.
            statements = _pick_grounded(rng, context, 1 if label == "clean" else 0)
            if label == "halluc":
                statements = statements + _pick_injected(rng, context)
            statements = [dict(s, text=rng.choice(s["texts"])) for s in statements]
            rng.shuffle(statements)
            text, propositions = build_note(statements)
            notes.append({
                "note_id": f"{label}-{number:04d}",
                "label": label,
                "patient_context": context,
                "text": text,
                "propositions": propositions,
            })
    return notes


def assign_splits(notes, seed):
    """40% of each class goes to dev, the rest to test. The test split is never used for threshold tuning (PLAN 4.3)."""
    rng = random.Random(seed + 1)
    dev, test = [], []
    for label in ("clean", "halluc"):
        ids = sorted(n["note_id"] for n in notes if n["label"] == label)
        rng.shuffle(ids)
        cut = round(len(ids) * DEV_FRACTION)
        dev.extend(ids[:cut])
        test.extend(ids[cut:])
    return {"seed": seed, "dev_fraction": DEV_FRACTION, "dev": sorted(dev), "test": sorted(test)}


def write_corpus(out, seed, n_clean, n_halluc):
    out = Path(out)
    for sub in ("notes", "gold", "patients"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    notes = generate_notes(seed, n_clean, n_halluc)
    splits = assign_splits(notes, seed)
    split_of = {nid: "dev" for nid in splits["dev"]} | {nid: "test" for nid in splits["test"]}
    for note in notes:
        data = note["text"].encode("utf-8")
        (out / "notes" / f"{note['note_id']}.txt").write_bytes(data)
        gold = {
            "note_id": note["note_id"],
            "split": split_of[note["note_id"]],
            "label": note["label"],
            "patient_context": note["patient_context"],
            "patient_ref": f"Patient/pat-{note['patient_context'].lower()}",
            "note_sha256": hashlib.sha256(data).hexdigest(),
            "char_length": len(note["text"]),
            "propositions": [dict(p, gold_id=f"{note['note_id']}-p{i + 1:02d}")
                             for i, p in enumerate(note["propositions"])],
        }
        (out / "gold" / f"{note['note_id']}.gold.json").write_text(
            json.dumps(gold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for context in sorted(CONTEXTS):
        (out / "patients" / f"{context}.json").write_text(
            json.dumps(patient_bundle(context), indent=2) + "\n", encoding="utf-8")
    (out / "splits.json").write_text(json.dumps(splits, indent=2) + "\n", encoding="utf-8")
    return notes, splits


def corpus_digest(corpus):
    """One SHA-256 over every corpus file, in path order. The run records it so a result names its corpus."""
    corpus = Path(corpus)
    digest = hashlib.sha256()
    for path in sorted(p for p in corpus.rglob("*") if p.is_file()):
        digest.update(path.relative_to(corpus).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--n-clean", type=int, default=200)
    parser.add_argument("--n-halluc", type=int, default=200)
    parser.add_argument("--out", default=str(ROOT / "benchmark" / "corpus" / "v1"))
    args = parser.parse_args(argv)
    notes, splits = write_corpus(args.out, args.seed, args.n_clean, args.n_halluc)
    print(f"wrote {len(notes)} notes to {args.out}: {len(splits['dev'])} dev, {len(splits['test'])} test")
    print(f"corpus sha256 {corpus_digest(args.out)}")


if __name__ == "__main__":
    main()
