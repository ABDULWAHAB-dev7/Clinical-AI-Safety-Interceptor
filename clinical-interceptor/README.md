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
| Audit | `store/postgres.py`, `store/records.py` | One PostgreSQL transaction per request, into the DATABASE.md schema. Records hold hashes, codes and pseudo-IDs only. |
| Fail-closed pipeline | `core/pipeline.py` | Built. Writes one note, six stage traces (INGEST, RETRIEVE, EXTRACT, VERIFY, ATTRIBUTE, INTERCEPT) and one event per CRITICAL or WARNING claim. Delivery requires the write to succeed and a non-blocking decision. |

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
python -m unittest discover -s tests -t .          # 104 tests; database tests skip without a DSN
uvicorn api.app:build_default_app --factory --host 127.0.0.1 --port 8000
```

Environment for the default app:

- `INTERCEPTOR_DATABASE_URL` (required): PostgreSQL DSN. The schema in `db/schema.sql` must already be applied.
- `INTERCEPTOR_PSEUDO_KEY` (required, at least 16 bytes): secret for patient and encounter pseudo-IDs. Never commit it.
- `INTERCEPTOR_POLICY`, `INTERCEPTOR_FHIR_DIR` (optional): default to `config/policy/default.yaml` and `fixtures/fhir/`.

The service refuses to start without the first two. `/readyz` returns 503 if the database is unreachable.

```bash
curl -s -X POST http://127.0.0.1:8000/v1/verify -H "Content-Type: application/json" -d @examples/verify_clean.json
```

## Layout

```
core/      domain models, errors, policy, decomposition, attribution, pipeline (no I/O imports)
fhir/      encounter bundle loading and parsing
nli/       deterministic verifiers (pure) and the NLI interface
store/     PostgreSQL repository and in-memory store for tests
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

- Encrypted object store for note text (`note_storage_uri` is nullable).
- Retention jobs (6 years, assumed), access roles, and row-level security.


## Pipeline to PostgreSQL

`verify_document` builds one `RequestRecord` in memory and writes it in a single transaction (`store/postgres.py`). Decisions:

- **Note row:** keyed by (patient pseudo-ID, SHA-256 of the text). Verifying the same text again updates the note and adds six more traces, so the history is kept.
- **Stages:** each stage writes one `audit_traces` row with a hash of its input and a payload of counts and codes. A failed stage marks later stages `SKIPPED`, and INTERCEPT always runs.
- **Events:** one per CRITICAL or WARNING claim, linked to the VERIFY trace. `claim_text` is a token such as `claim:medication:negated`, not draft text, because DATABASE.md leaves clear-text storage open.
- **No patient, no record:** a request whose patient cannot be resolved is never delivered and writes nothing, because the note row needs a patient pseudo-ID.
- **Audit failure:** any database error becomes `AUDIT_WRITE_FAILURE`, which blocks delivery.

Known gaps in this version: this document-level pipeline (`core/pipeline.py`) still uses the older name-matching verifier, so it never fires rule 1. The newer triple-based verifier (see "Verification core") is used by `/v1/interceptor/verify`. NLI-based verdicts need a model, which is still not approved, so free-text claims that need it block.

## API (API.md v1.0.0): first milestone

Built, with 235 tests (the live PostgreSQL tests run when `INTERCEPTOR_TEST_DSN` is set):

| Area | Endpoint or part | Status |
|---|---|---|
| Health | `GET /v1/health` (no auth), `GET /v1/health/ready` (503 if the DB is down) | Built |
| Errors | Envelope from 11.1, codes table from 11.2 (subset), no text or IDs in messages | Built |
| Headers | `X-Request-Id` (echoed if a UUID), `X-Interceptor-Version: 1.0.0`, `X-Interception-Time-MS` | Built |
| Limits | 2 MiB body check (Content-Length), JSON content type (415) | Built |
| Auth | Bearer JWT, ES256 or RS256, audience `clinical-interceptor`, lifetime at most 15 min, scopes (2.2) | Built. Default is deny-all. |
| Decompose | `POST /v1/interceptor/decompose`: 201 for a new note, 200 for a duplicate | Built |
| Verify | `POST /v1/interceptor/verify`: `VerificationResult` shape, optional VERIFY trace | Built |
| Legacy | `POST /v1/verify` (full pipeline, now requires a token) | Kept |

Not built: the proxy `/v1/chat/completions/intercept` (the upstream provider is undecided), `/v1/interceptor/audit` and the report and note reads, idempotency replay (8.3), mTLS, rate limiting, and streaming.

### Configuration

- `INTERCEPTOR_JWT_PUBLIC_KEY_FILE`: PEM public key for bearer tokens. If unset, every token is refused.
- Other variables as before: `INTERCEPTOR_DATABASE_URL`, `INTERCEPTOR_PSEUDO_KEY`, `INTERCEPTOR_POLICY`, `INTERCEPTOR_FHIR_DIR`.

### Blocking gaps before any deployment

1. **mTLS is not enforced.** API.md 2.1 requires a bearer token and a client certificate on every clinical endpoint. Bearer is enforced here, and mTLS must be enforced at the internal load balancer. Do not expose the service to clients until this is in place.
2. **Some claims need the NLI model.** A claim with unresolved candidates (for example one that only shares a word with a fact) returns `503 INTERCEPT_VERIFIER_UNAVAILABLE`. One such claim fails the whole request.
3. **Idempotency-Key is validated but replay is not stored.** A duplicate note returns the existing note ID because of the database uniqueness rule, but a reused key with a different body is not rejected with 409.

### Conflicts and choices in API.md (please review)

- **401 vs 403 for a missing token:** section 10 says 403, the error table says 401. This build uses 401 (`AUTH_TOKEN_INVALID`) for missing, expired, or badly signed tokens, and 403 (`AUTH_SCOPE_INSUFFICIENT`) for a missing scope.
- **Retryable column:** garbled in the PDF extraction. Values follow what each code means.
- **Entities:** decompose links entities from the local lexicon (`knowledge/lexicon_seed.csv`, 8 synthetic entries). A claim with no match keeps `entities: []`. The lexicon needs clinical review and is far from complete.
- **Claim IDs:** decompose uses the spec's derivation (5.1.4). The events written by `/v1/verify` (the legacy pipeline) still use the earlier derivation, so the two do not match yet.
- **Offsets in the examples:** API.md's own example offsets (48–85, 66–80) have the same errors as DATABASE.md 4.4. The API computes offsets from the text.
- **Policy override:** `min_confidence_entailment` is accepted. The deterministic verifier's confidence is always 1.0, so the threshold has no effect yet.
- **Rule 1 (drug-allergy conflict):** fires in `/v1/interceptor/verify` for drugs in `knowledge/drug_class_map.yaml` (only amoxicillin to penicillin so far). A drug with no class is not checked, and the trace records `drug_class_unknown`.
- **Limits:** the 2 MiB check reads the Content-Length header only. Chunked bodies are not limited yet.
- **Paths changed:** `/healthz` and `/readyz` are replaced by `/v1/health` and `/v1/health/ready`.
- **Non-spec warning code:** `INTERCEPT_CLAIM_SKIPPED` marks a sentence that produced no usable claim. The spec does not define it.


## Verification core (PLAN phases 2.4, 3.3, 4.2 to 4.6, without the NLI model)

`/v1/interceptor/verify` now verifies claims against ground-truth triples built from the request's FHIR bundle.

| Part | Where | Notes |
|---|---|---|
| Knowledge files | `knowledge/` | Lexicon, drug classes, dose limits, thresholds. All synthetic, marked "requires clinical review". Loaded at startup; a bad file stops the service. |
| Triples | `fhir/triples.py` | HAS_ALLERGY, HAS_CONDITION, HAS_OBSERVATION, HAS_ENCOUNTER. Excluded facts (entered-in-error, refuted) are never triples. Case-0001 yields exactly 4. |
| Entity linking | `core/entities.py` | Longest match first, word boundaries, code-point offsets. `amoxicillin-clavulanate` does not yield amoxicillin. |
| Retrieval | `core/retrieval.py` | Up to 5 triples, scored +3 code, +2 drug class, +1 per shared content token. |
| Dose check | `core/dosage.py` | Limit check only (single and daily). No comparison with documented doses. |
| Verifier | `core/verify.py` | Branches, rules 1 and 2, NLI interface with a timeout. Every result is validated against the `VerificationResult` contract. |
| Gold check | `tools/gold_check_verify.py` | `python tools/gold_check_verify.py` |

How a claim is classified, in order: medication claims (NEUTRAL, or CONTRADICTION if the dose is over the limit), allergy-concept claims, coded condition and lab matches, no candidates (NEUTRAL), and anything else goes to the NLI scorer. With no scorer installed, an unresolved claim raises a `VerificationError` and the request fails closed. A timeout, an error, malformed scores, or out-of-range scores fail closed the same way.

### Gold check status

`python tools/gold_check_verify.py` reports 4 of 5 for case-0001. Claim 5 ("Blood pressure is stable") needs the NLI scorer: the plan's token-overlap scoring gives the HbA1c fact a +1 for the shared word "blood", so a candidate is left unresolved. With a stub scorer returning NEUTRAL, all 5 match. This is tested. It is not a bug, but it is a real dependency on Gate G1, and it shows that the plan's retrieval over-recalls on generic words.

### Deviations from PLAN.md (please review)

- **Allergy-concept extension in retrieval:** a claim naming a general allergy concept (SNOMED 416098002) gets +3 against allergy triples. Without it, "denies known drug allergies" shares no code with "allergy to penicillin" and could never reach it.
- **Contradiction precedence across candidates:** the plan picks the candidate with the highest entailment. This build also returns CONTRADICTION if any candidate clears the contradiction threshold, because a missed contradiction is the worst failure.
- **Deterministic shortcuts:** coded condition and lab matches, and medication claims, are resolved without the NLI model. The plan routes everything through NLI. Architecture 3.5 allows deterministic-first routing.
- **Medication claims are always NEUTRAL** unless the dose is over the limit, because the FHIR subset has no medication facts. Rule 1 still sets CRITICAL on top of that, and the NLI class stays NEUTRAL as in the gold labels.
- **Gold offsets:** the plan's required test for "drug allergies" (offsets 66 to 80) is wrong for the actual note. The text has it at 69 to 83, which is what the code and tests use.
- **The gold check runs the verifier on gold claims and gold entities.** The plan's full decompose-then-verify check is not possible yet: the rule-based decomposer does not reproduce the gold claim spans (for example the gold claim for "type 2 diabetes" starts at 31, after the prefix "Follow-up for").
- **Layout:** these modules are in the current `core/`, `fhir/` layout, not `src/interceptor/`. The migration waits on preflight.
