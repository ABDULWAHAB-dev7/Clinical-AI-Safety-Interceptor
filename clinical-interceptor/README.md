# Clinical Interceptor (architecture v0.1, MVP slice)

Verifies a generated clinical document against the patient's FHIR record before it reaches a clinician or EHR. Every failure resolves to quarantine, never to delivery. Start with [the architecture](../ARCHITECTURE.pdf) and the PRD; this README describes what is built.

> Synthetic data only. Not validated, not a medical device, not for patient care.

## What is built

| Stage (architecture 3) | Module | Status |
|---|---|---|
| 1. Ingestion (`/v1/verify`, size cap, scope check, request validation) | `api/app.py` | Built. Proxy endpoints are not built. |
| 2. Decomposition | `core/decompose.py` | Rule-based stand-in: sentence segmentation, section headers, negation, typing. The LLM decomposer and Med-spaCy are not built. |
| 3. Ground-truth retrieval | `fhir/bundle.py` | Reads a local FHIR Bundle fixture. The SMART-authenticated client is not built. |
| 4. Verification matrix | `nli/deterministic.py`, `nli/model.py` | Deterministic verifiers for allergy, medication and lab. NLI is an interface only, with no model loaded. |
| 5. Attribution spans | `core/attribution.py` | Built, with precedence merge. |
| 6. Decision engine | `core/policy.py` | Hard-stop rules R1 to R4 in code; configurable rule C1 from the policy file. |
| Audit | `store/audit.py` | Hash-chained JSONL, references and hashes only. PostgreSQL is not built. |
| Fail-closed pipeline | `core/pipeline.py` | Built. Delivery requires a persisted audit record and a non-blocking decision. |

Outcomes: `PASS_THROUGH` (delivered), `WARN` (delivered with ungrounded spans to acknowledge), `HARD_BLOCK` (not delivered, no text returned).

Verdicts: `SUPPORTED`, `UNGROUNDED`, `CONTRADICTED`, `EXCLUDED`, `VERIFICATION_ERROR`. `EXCLUDED` is defined but not yet assigned; it needs the FR-2.1 rule, which is not in the PRD copy I have.

### Hard-stop rules (in code, cannot be disabled by policy)

| Rule | Fires when |
|---|---|
| R1 | Any pipeline error, including FHIR parse failure, retrieval failure, or audit write failure |
| R2 | Any `VERIFICATION_ERROR` (fail-closed default) |
| R3 | Any `CONTRADICTED` assertion |
| R4 | The document yields no assertions |

## Run

Python 3.11+ (tested on 3.12).

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -t .          # 59 tests
uvicorn api.app:build_default_app --factory --host 127.0.0.1 --port 8000
```

Environment variables for the default app: `INTERCEPTOR_POLICY`, `INTERCEPTOR_FHIR_DIR`, `INTERCEPTOR_AUDIT`. Defaults point at `config/policy/default.yaml`, `fixtures/fhir/` and `var/audit.jsonl`.

```bash
curl -s -X POST http://127.0.0.1:8000/v1/verify -H "Content-Type: application/json" -d @examples/verify_clean.json
```

## Layout

```
core/      domain models, errors, policy, decomposition, attribution, pipeline (no I/O imports)
fhir/      encounter bundle loading and parsing
nli/       deterministic verifiers (pure) and the NLI interface
store/     append-only audit store
api/       FastAPI app and uvicorn factory
config/    versioned policy file
fixtures/  synthetic FHIR bundles
examples/  sample requests (clean and contradicted)
tests/     unit, contract (hard-stop rules), fail_mode (failure injection), API
docs/policy-changes/  change records for hard-stop rule releases (FR-5.2)
```

Deviations from the architecture's directory tree: `tests/` uses `unit/`, `contract/`, `fail_mode/`, and the API test sits in `unit/`. `core/` is flat (no `decompose/`, `policy/` or `ids.py` subpackages yet). Both are tidy-up items.

## Blocked before build (architecture 10.1)

These block the parts not built. The code exposes an interface for each so it can be added without touching the fail-closed rules.

1. **NLI model selection and license.** Without a model, NLI-routed assertions become `VERIFICATION_ERROR` and block. Free-text claims (plans, findings, conditions) therefore always block today.
2. **Review-store retention design.** Span text is returned only for delivered documents. No review store exists yet, so the dashboard cannot display the attributed document.
3. **Upstream BAA coverage.** Proxy mode is not built.

## Open questions

- **PRD sections 5 and FR-5.x are not in the PRD copy I have.** The architecture refers to them; the rules here follow architecture section 7.2 and 3.7 instead.
- **Lab tolerance and unit conversion tables** (PRD open question 2). The tolerance is `0.0` placeholder; units must match exactly; there is no conversion.
- **Thresholds** (`config/policy/default.yaml`) are placeholders until the model is calibrated.
- **Missing resource types** produce per-assertion `VERIFICATION_ERROR`, but FHIR parse failures block the whole document (architecture 7.1 and 7.2 #3 differ). This build follows both: parse failures block, missing types affect only dependent assertions.
- **Audit schema** is a placeholder. The architecture's PostgreSQL schema is not built.
- **Latency budget** (800 ms p95) is not measured. No benchmark exists yet.
- **Approver roles and override flow** (`/v1/override`) are not built.

## Database layer (DATABASE.md)

| Part | Location | Status |
|---|---|---|
| FHIR R4 ingestion models (Patient, Observation, AllergyIntolerance, Condition, Encounter) | `fhir/models.py` | Built |
| Ingestion rules R1 to R4 | `fhir/ingest.py` | Built. R2 and R4 text was partly lost in the PDF; mapping noted in the module. |
| Verification contracts (MedicalEntity, AtomicProposition, VerificationResult, AttributionSpan) | `core/contracts.py` | Built, with validators |
| Severity policy (priorities 1 to 6) | `core/severity.py` | Built. The NLI class is never changed by a rule. |
| Keyed pseudo-IDs (`pid_`, `eid_`, HMAC-SHA256) | `core/pseudo.py` | Built. The seed uses an obviously fake dev key. |
| PostgreSQL schema: ENUMs, tables, append-only triggers, indexes, `v_hallucination_current` | `db/schema.sql` | Built and applied on PostgreSQL 18 |
| Repository (inserts, latest review state) | `store/postgres.py` | Built |
| Synthetic seed case-0001 (FHIR ndjson, note, gold labels) | `seed/` | Built |
| Acceptance checks S1 to S5 | `tools/seed_check.py` | Built, all pass |

### Run the database tests

Use a disposable database. The DB tests drop and recreate its `public` schema.

```bash
createdb -h 127.0.0.1 -p 55432 -U postgres interceptor_test
INTERCEPTOR_TEST_DSN="host=127.0.0.1 port=55432 dbname=interceptor_test user=postgres" python -m unittest discover -s tests -t .
python tools/seed_check.py
```

Without `INTERCEPTOR_TEST_DSN`, the 8 database tests are skipped.

### Changes from DATABASE.md (please review)

- **Gold offsets were regenerated.** The hand-computed offsets in section 4.4 did not match the note text. For example, "drug allergies" is at 69 to 83, not 66 to 80, and the S2 sentence ends at 84, not 85. The offsets are now computed from the text, and S4 checks them. A test shows the original values fail S4.
- **Added gold fields** `confidence` and `is_clinical`, needed to evaluate the severity policy. `source_sentence` is stored so S4 can check it.
- **The sentence `Chief complaint:` is treated as a section header**, so S1 starts at offset 17, as in the doc.
- **Terminology is a placeholder.** The RxNorm-to-allergy-class mapping (amoxicillin to penicillin) covers only case-0001 and must be replaced by a governed lookup.
- **The `v_hallucination_current` view** uses a LATERAL join, so events with no review show as `OPEN`. The doc's `DISTINCT ON` version would hide them.
- **Row-level security, analyst roles, and grants** from section 3.6 are not in the schema. The triggers do enforce append-only.

### Not built

- Writing a full request through the pipeline into PostgreSQL. `core/pipeline.py` still uses the JSONL audit log.
- Encrypted object store for note text (`note_storage_uri` is nullable).
- Retention jobs (6 years, assumed), access roles, and row-level security.
