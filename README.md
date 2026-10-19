# Clinical-AI-Safety-Interceptor

A checkpoint that sits between an AI system's proposed clinical output and the clinician who would act on it. It runs deterministic rule checks and returns one decision: `allow`, `review`, or `block`, with the reasons.

> **Prototype.** The reference data in `data/` is synthetic placeholder data for demonstrating the rules. It is not a clinical formulary, has not been validated, and is not a medical device. Do not use it for patient care.

## What it checks

| Rule | Severity | Meaning |
|---|---|---|
| `missing_patient_id` | block | Output cannot be tied to a patient |
| `allergy_conflict` | block | Ordered drug matches a recorded allergy |
| `dose_over_single_max` | block | Single dose above the formulary limit |
| `dose_over_daily_max` | block | Dose times frequency above the daily limit |
| `unknown_drug` | review | Drug not in the formulary, so the dose cannot be verified |
| `interaction` | review | Ordered drug pair listed in the interaction table |
| `overclaim` | review | Unqualified safety language such as "definitely safe" |

Decision: any `block` finding gives `block`; otherwise any finding gives `review`; otherwise `allow`.

## Request format

```json
{
  "patient": {"id": "SYN-001", "allergies": ["penicillin"], "medications": []},
  "output": {
    "text": "Paracetamol 500 mg every 6 hours as needed.",
    "orders": [{"drug": "paracetamol", "dose_mg": 500, "frequency_per_day": 4}]
  }
}
```

## Run

Standard library only; Python 3.10+.

```bash
python -m interceptor check examples/clean.json    # exit 0 = allow
python -m interceptor check examples/unsafe.json   # exit 2 = block
python -m interceptor serve 8001                   # POST http://127.0.0.1:8001/api/check
```

Exit codes for `check`: 0 allow, 1 review, 2 block (or invalid input).

## Test

```bash
python -m unittest discover -s tests -v
```

## Layout

```
interceptor/rules.py     individual checks
interceptor/engine.py    validation, running the rules, the decision
interceptor/server.py    stdlib HTTP endpoint (binds 127.0.0.1 only)
interceptor/__main__.py  CLI
data/*.sample.json       synthetic formulary and interaction table
examples/                sample requests
tests/                   unit and example-file tests
```

## Not yet built

- Real, licensed reference data and a source/version for every limit
- Authentication, audit logging, and rate limiting for the HTTP endpoint
- Integration with a specific clinical AI system or EHR
- Clinical validation with clinicians
