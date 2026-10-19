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

---

## Document gate (PRD v0.1)

`gate/` implements the PRD: it checks a generated clinical document (SOAP note, discharge summary, report draft) against the patient record before it reaches the EHR or signing queue. It flags and gates; a clinician remains the final authority.

| Journey | Condition | Status |
|---|---|---|
| A | All spans supported | `ready_for_review` |
| B | Any span ungrounded (no record evidence), none contradicted | `flagged_for_acknowledgment`; each flagged span must be accepted, edited or rejected before signing |
| C | Any span contradicted by the record (polarity conflict) | `quarantined`; not delivered; override needs a reason and approver ID |
| D | Verification error or timeout | `quarantined_verification_unavailable` (fail-closed). Fail-open only with a `FailOpenRule` per facility and document type, logged as `released_unverified` |

Every gate result, acknowledgment and override is written to a hash-chained JSONL audit log (`gate/audit.py`).

```bash
python -m gate verify examples/draft_clean.json examples/record.json --facility Facility-A --audit audit.jsonl
python -m gate verify examples/draft_flagged.json examples/record.json --facility Facility-A   # exit 1
python -m gate verify examples/draft_contradicted.json examples/record.json --facility Facility-A  # exit 2
python -m gate audit-verify audit.jsonl
```

Python API: `run_gate`, `ReviewSession`, `override`, `FailOpenRule`, `AuditLog` (see `gate/__init__.py`).

### Open questions from the PRD (not implemented; no behaviour assumed)

- **Section 5 / FR-5.x is missing from the PRD.** The hard-stop rules (FR-5.2), the override path details (FR-5.4) and the non-clinical classification (5.2) are not specified. Only polarity conflicts are hard stops today.
- **Latency budget** is not given, so no timeout is enforced; verification errors are the only fail-mode trigger.
- **NLI model** for "high-confidence contradiction" is not chosen; verification is rule-based and lexical.
- **Atomic assertion decomposition** is sentence-level. Unit, date and value checks (for example a lab value with the wrong unit) are not implemented.
- **Encounter record source**: FHIR integration is required by the CIO but no resource mapping is given. `examples/record.json` is a flat synthetic format.
- **Audit schema** is a placeholder format.
- **Who may approve overrides and fail-open** (roles, Compliance workflow) is not defined.
