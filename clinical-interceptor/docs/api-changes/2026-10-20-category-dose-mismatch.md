# API change: hallucination category for dose claims (API.md 7.1.2)

Status: implemented in code and tests. The API.md original is not in this repository, so the same edit must be made
there (see "Source document" below).

## Why

Row 1 of 7.1.2 sent every CONTRADICTION that named a drug in an allergy class to `DRUG_ALLERGY_CONFLICT`. That included a
dose above the limit for a patient with no matching allergy, for example "Started amoxicillin 1500 mg once." The
severity and risk rule were right (`CRITICAL`, `contradiction_high_risk`), but the category said the patient had an
allergy conflict, which the record does not show. A clinician reading the event would be misled.

## Before (7.1.2, as written)

| Condition (evaluated in order) | category |
| --- | --- |
| risk_rule = drug_allergy_conflict, or a CONTRADICTION where an entity is an allergy or a drug in an allergy class | DRUG_ALLERGY_CONFLICT |
| CONTRADICTION where an entity is a medication with a dose or frequency | MEDICATION_DOSE_MISMATCH |
| CONTRADICTION where an entity is an active diagnosis | DIAGNOSIS_CONTRADICTION |
| CONTRADICTION or NEUTRAL where the matched fact is an Observation | LAB_VALUE_MISMATCH |
| Claim references a date inconsistent with its matched fact | TEMPORAL_MISMATCH |
| risk_rule = unsupported_clinical | UNSUPPORTED_CLAIM |
| Anything else | OTHER |

## After

| Condition (evaluated in order) | category |
| --- | --- |
| risk_rule = drug_allergy_conflict, or a CONTRADICTION where an entity is an allergy concept | DRUG_ALLERGY_CONFLICT |
| CONTRADICTION where an entity is a medication with a dose or frequency | MEDICATION_DOSE_MISMATCH |
| CONTRADICTION where an entity is a drug in an allergy class (no dose or frequency stated) | DRUG_ALLERGY_CONFLICT |
| CONTRADICTION where an entity is an active diagnosis | DIAGNOSIS_CONTRADICTION |
| CONTRADICTION or NEUTRAL where the matched fact is an Observation | LAB_VALUE_MISMATCH |
| Claim references a date inconsistent with its matched fact | TEMPORAL_MISMATCH |
| risk_rule = unsupported_clinical | UNSUPPORTED_CLAIM |
| Anything else | OTHER |

The only change is that a dose or frequency on a drug in an allergy class is now checked before the allergy-class clause.
A matched allergy (rule 1, `drug_allergy_conflict`) still gives `DRUG_ALLERGY_CONFLICT` regardless of dose.

## Effect

- "Started amoxicillin 500 mg TID" with a documented penicillin allergy: `DRUG_ALLERGY_CONFLICT`, unchanged.
- "Started amoxicillin 1500 mg once" with no penicillin allergy: `MEDICATION_DOSE_MISMATCH` (was `DRUG_ALLERGY_CONFLICT`).
- "Started amoxicillin" (no dose) with no penicillin allergy: `DRUG_ALLERGY_CONFLICT`, unchanged.
- Allergy denial, "Patient denies known drug allergies": `DRUG_ALLERGY_CONFLICT`, unchanged.

No enum value or field changes. The hallucination category enum is the same, so this is a mapping change, not a DATABASE
or API schema change.

## Tests

- `tests/unit/test_categories.py`: rows 2 and 2a, and the order of the allergy-class row against the dose row.
- The benchmark (`benchmark/`) already labelled the dose claims `MEDICATION_DOSE_MISMATCH`. Its category accuracy moved
  from 0.87 to 1.00 on both splits.

## Source document

Edit the API.md 7.1.2 row "risk_rule = drug_allergy_conflict, or a CONTRADICTION where an entity is an allergy or a drug in
an allergy class" to read as in "After", and add the row "CONTRADICTION where an entity is a drug in an allergy class
(no dose or frequency stated)".
