# Policy change: NLI contradictions block only above P 0.80

Date: 2026-10-20
Requested by: the project owner (Phase 2 brief).

## Rules changed

- **Priority 2 (`contradiction_high_risk`)**: an NLI contradiction in a high-risk class (allergy, medication dose, active
  diagnosis) is CRITICAL only when its probability is **above 0.80** (`contradiction_critical_min` in
  `knowledge/thresholds.yaml`). At or below 0.80 it is a WARNING (`contradiction`).
- **Deterministic contradictions are unchanged.** They carry confidence 1.0, so they still block. This covers the coded
  allergy, condition and lab branches and the dose-limit rule.
- **Priority 1 (`drug_allergy_conflict`) is unchanged.** It is deterministic and blocks.
- **Priority 4 (NEUTRAL to `unsupported_clinical`)** was already a WARNING and does not block. This record confirms it.

## Reason

NLI general-domain models score clinical shorthand as NEUTRAL, and a NEUTRAL should warn, not block. A contradiction
should block only when the model is confident, so the block threshold moves from P 0.50 to P 0.80.

## Clinical reviewer

**None assigned.** This change affects which findings block a note. A clinician must review it before any use outside
this project.

## Test evidence

- `tests/unit/test_contracts_severity.py`: 0.95 blocks, 0.80 and 0.6 warn, a deterministic 1.0 still blocks, and NEUTRAL
  at 0.99 warns.
- Full suite: 501 tests pass (non-DB), and the DB suite passed earlier.

## Limits of this change (read before relying on it)

The real model (`cross-encoder/nli-deberta-v3-base`, lock in `nli_service/MODEL.lock`) was checked on eight pairs. Two
results matter:

1. **False contradiction above 0.80.** "Blood pressure is stable." against "Diabetes mellitus type 2, active." came back
   CONTRADICTION at 0.99. That would block at the new threshold. Retrieval normally passes only overlapping triples, so it
   may not reach this in practice, but the threshold does not prevent it.
2. **Correct statement scored NEUTRAL.** "HbA1c was 8.2%" against the record's 8.2 came back NEUTRAL at 1.00. Under the
   NEUTRAL rule it warns, which is wrong.

The 0.80 value is a directive, not a calibration. No labelled clinical pairs exist here, so the threshold has not been
calibrated, and the probabilities have not been shown to be reliable on clinical text.

## Policy version

`config/policy/default.yaml` is unchanged at its version. The thresholds file gained a key, so the version needs a bump
at the next release.
