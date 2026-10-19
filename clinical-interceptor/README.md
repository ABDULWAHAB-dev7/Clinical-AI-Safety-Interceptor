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
