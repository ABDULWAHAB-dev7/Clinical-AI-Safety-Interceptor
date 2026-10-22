# Clinical Interceptor (architecture v0.1, MVP slice)

Verifies a generated clinical document against the patient's FHIR record before it reaches a clinician or EHR. Every failure resolves to quarantine, never to delivery. Start with [the architecture](../ARCHITECTURE.pdf) and the PRD; this README describes what is built.

> Synthetic data only. Not validated, not a medical device, not for patient care.

## What is built

| Stage (architecture 3) | Module | Status |
|---|---|---|
| 1. Ingestion (`/v1/verify`, size cap, scope check, request validation) | `api/app.py`, `api/document.py` | Built. `/v1/verify` runs the same engine as the `/v1/interceptor` endpoints (`api/engine.py`). |
| 2. Decomposition | `core/decompose.py` | Rule-based stand-in: sentence segmentation, section headers, negation, typing. The LLM decomposer and Med-spaCy are not built. |
| 3. Ground-truth retrieval | `fhir/bundle.py` | Reads a local FHIR Bundle fixture. The SMART-authenticated client is not built. |
| 4. Verification matrix | `core/verify.py`, `nli_service/scorer.py` | Triple-based verifier for allergy, condition, lab and medication claims. The NLI scorer loads the approved checkpoint (see `nli_service/MODEL.lock`). |
| 5. Attribution spans | `core/attribution.py` | Built, with precedence merge. |
| 6. Decision engine | `core/policy.py` | Hard-stop rules R1 to R4 in code; configurable rule C1 from the policy file. |
| Audit | `store/postgres.py`, `store/records.py` | One PostgreSQL transaction per request, into the DATABASE.md schema. Records hold hashes, codes and pseudo-IDs only. |
| Fail-closed pipeline | `api/engine.py` | Built. One note row, then EXTRACT, VERIFY and ATTRIBUTE traces, and one event per CRITICAL or WARNING claim. Delivery requires the write to succeed and a non-blocking decision. |

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
pytest          # database tests skip without INTERCEPTOR_TEST_DSN
```

To run the service, see "Run it for real (local)" in the API section. It needs a migrated PostgreSQL database, the
pseudonym key, a JWT public key and a client certificate registry; the service refuses to start without the database URL
and the key, and refuses every clinical request until the registry and the token key are configured.

## Layout

```
src/interceptor/
  core/      domain models, errors, policy, decomposition, attribution, pipeline (no I/O imports)
  fhir/      encounter bundle loading and parsing
  nli/       the NLI scorer (loads the approved local checkpoint, checks its lock)
  store/     PostgreSQL repository and in-memory store for tests
  api/       FastAPI app, auth, endpoints and uvicorn factory
  intercept/ proxy release policy, analysis, upstream client, streaming
  paths.py   PROJECT_ROOT (override with INTERCEPTOR_HOME) for data directories
knowledge/ synthetic lexicon and thresholds (require clinical review)
db/        schema.sql;  migrations/  Alembic
web/       Next.js dashboard (phase 6), see web/README.md
config/    versioned policy file
fixtures/  synthetic FHIR bundles
examples/  sample requests (clean and contradicted)
tests/     unit, contract (hard-stop rules), fail_mode (failure injection), API
docs/policy-changes/  change records for hard-stop rule releases (FR-5.2)
```

Module names below are relative to `src/interceptor/`. Deviations from the architecture's directory tree: `tests/` uses `unit/`, `contract/`, `fail_mode/`, and the API test sits in `unit/`. `core/` is flat (no `decompose/`, `policy/` or `ids.py` subpackages yet). Both are tidy-up items.

## Blocked before build (architecture 10.1)

These block the parts not built. The code exposes an interface for each so it can be added without touching the fail-closed rules.

1. **NLI model selection and license.** Without a model, NLI-routed assertions become `VERIFICATION_ERROR` and block. Free-text claims (plans, findings, conditions) therefore always block today. The checkpoint `cross-encoder/nli-deberta-v3-base` is approved and locked in `nli_service/MODEL.lock` (the weights are downloaded locally, never committed) and runs as a separate service; see [docs/nli-service.md](docs/nli-service.md).
2. **Review-store retention design.** Span text is returned only for delivered documents. No review store exists yet, so the dashboard cannot display the attributed document.
3. **Upstream BAA coverage.** The proxy is built, but the prompt is sent to the upstream model, so a BAA (or a locally hosted model) is needed before it carries real PHI.

## Open questions

- **PRD sections 5 and FR-5.x are not in the PRD copy I have.** The architecture refers to them; the rules here follow architecture section 7.2 and 3.7 instead.
- **Lab tolerance and unit conversion tables** (PRD open question 2). The tolerance is `0.0` placeholder; units must match exactly; there is no conversion.
- **Thresholds** (`config/policy/default.yaml`) are placeholders until the model is calibrated.
- **Missing resource types** produce per-assertion `VERIFICATION_ERROR`, but FHIR parse failures block the whole document (architecture 7.1 and 7.2 #3 differ). This build follows both: parse failures block, missing types affect only dependent assertions.
- **Audit store:** DATABASE.md's PostgreSQL schema is built (`db/schema.sql`, Alembic revision 0001). The older JSONL log is gone.
- **Latency budget** (800 ms p95) is not measured. No benchmark exists yet.
- **Approver roles.** The override and acknowledgment endpoints exist, and the reviewer is a pseudonym of a self-asserted signature, not a verified identity. Who may approve is an open decision (`docs/decisions.md`).

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
INTERCEPTOR_TEST_DSN="host=127.0.0.1 port=55432 dbname=interceptor_test user=postgres" pytest
python tools/seed_check.py
```

Without `INTERCEPTOR_TEST_DSN`, the 8 database tests are skipped.

### Changes from DATABASE.md (please review)

- **Gold offsets were regenerated.** The hand-computed offsets in section 4.4 did not match the note text. For example, "drug allergies" is at 69 to 83, not 66 to 80, and the S2 sentence ends at 84, not 85. The offsets are now computed from the text, and S4 checks them. A test shows the original values fail S4.
- **Added gold fields** `confidence` and `is_clinical`, needed to evaluate the severity policy. `source_sentence` is stored so S4 can check it.
- **The sentence `Chief complaint:` is treated as a section header**, so S1 starts at offset 17, as in the doc.
- **Terminology is a placeholder.** The RxNorm-to-allergy-class mapping (amoxicillin to penicillin) covers only case-0001 and must be replaced by a governed lookup.
- **The `v_hallucination_current` view** uses a LATERAL join, so events with no review show as `OPEN`. The doc's `DISTINCT ON` version would hide them.
- **Roles, grants and row-level security** are in `db/roles.sql` (apply after the schema; tested in the CI database job). The triggers enforce append-only regardless.

### Not built

- Encrypted object store for note text (`note_storage_uri` is nullable).
- Automatic deletion. `scripts/retention.py` reports and exports records past the retention period (6 years assumed) and never deletes; removal is a separate step for the database owner.


## Pipeline to PostgreSQL

`/v1/verify` and the `/v1/interceptor` endpoints share `api/engine.py`. A document is written as one note row, then its traces, in the audit store (`store/postgres.py`). Decisions:

- **Note row:** keyed by (patient pseudo-ID, SHA-256 of the text). Verifying the same text again reuses the note and adds its VERIFY and ATTRIBUTE traces, so the history is kept.
- **Stages:** each stage writes one `audit_traces` row with a hash of its input and a payload of counts and codes. A failed stage marks later stages `SKIPPED`, and INTERCEPT always runs.
- **Events:** one per CRITICAL or WARNING claim, linked to the report (ATTRIBUTE) trace. `claim_text` is a token such as `claim:medication:negated`, not draft text, because DATABASE.md leaves clear-text storage open.
- **No patient, no record:** a request whose patient cannot be resolved is never delivered and writes nothing, because the note row needs a patient pseudo-ID.
- **Audit failure:** any database error becomes `AUDIT_WRITE_FAILURE`, which blocks delivery.

The older name-matching document pipeline has been retired. `/v1/verify` now gives the same verdicts as the three-call flow, and it fails closed on the same errors. Claims that need the NLI model block until the server is started with `INTERCEPTOR_NLI_MODEL_PATH` and its lock.

## API (API.md v1.0.0)

Every endpoint in API.md is built, except where an open decision is listed below.

| Area | Endpoint or part | Status |
|---|---|---|
| Health | `GET /v1/health` (no auth), `GET /v1/health/ready` (503 if the database is down) | Built |
| Proxy | `POST /v1/chat/completions/intercept`, non-streaming and streaming (SSE) | Built |
| Decompose | `POST /v1/interceptor/decompose` (201 new, 200 replay, 409 key reused) | Built |
| Verify | `POST /v1/interceptor/verify` | Built |
| Audit | `POST /v1/interceptor/audit` (201, 200 replay, 409, 422, 503) | Built |
| Reads | `GET /v1/interceptor/notes/{id}`, `GET /v1/interceptor/audit/reports/{id}` | Built |
| Override | `POST /v1/interceptor/override` (a clinician reviews a critical finding; [record](docs/api-changes/2026-10-20-override.md)) | Built |
| Acknowledge | `POST /v1/interceptor/acknowledge` (accept, edit or reject a warning), `GET /v1/interceptor/audit/reports/{id}/review-status` ([record](docs/api-changes/2026-10-22-acknowledge.md)) | Built |
| Quarantine | `GET /v1/interceptor/quarantine`, plus a best-effort webhook (`INTERCEPTOR_NOTIFY_WEBHOOK_URL`) and a stored `FAILED` status when verification is unavailable ([record](docs/api-changes/2026-10-22-quarantine.md)) | Built |
| Document check | `POST /v1/verify` (one call: decompose, verify, audit, on the shared engine; described in ARCHITECTURE 6.2, not in API.md) | Built, behind the same auth |
| Errors | Envelope from 11.1, the codes in use from 11.2 | Built |
| Headers | `X-Request-Id`, `X-Interceptor-Version`, `X-Interception-Time-MS`, `X-Interception-Status`, `X-Trace-Id`, `X-Upstream-Time-MS`, `RateLimit-*`, `Retry-After` | Built |
| Limits | 2 MiB body, note and bundle limits, 100 messages, JSON content type, 60 requests per minute per client | Built |
| Migrations | Alembic revision 0001 applies `db/schema.sql` unchanged | Built |

### Security (every clinical endpoint needs both factors)

1. **Client certificate.** The certificate fingerprint must be in the client registry. In production, a reverse proxy terminates mTLS and forwards the SHA-256 fingerprint in `X-Client-Cert-SHA256` with a shared secret in `X-Proxy-Secret`. The application trusts those headers only from a trusted proxy address with a matching secret. Anything else counts as no certificate (`403 AUTH_MTLS_REQUIRED`). An empty registry refuses everything.
2. **Bearer token.** ES256 or RS256, audience `clinical-interceptor`, lifetime at most 15 minutes, with the scope for the endpoint (`401 AUTH_TOKEN_INVALID`, `403 AUTH_SCOPE_INSUFFICIENT`).
3. **Rate limit** per certificate identity (`429 RATE_LIMITED` with `Retry-After`).

The certificate is checked first, then the token, then the scope, so an unauthenticated caller never sees a schema error. For local development, `scripts/dev_server.py` terminates TLS in uvicorn itself, using `api/dev_tls.py` to hand the client certificate to the application.

### Configuration

| Variable | Purpose |
|---|---|
| `INTERCEPTOR_DATABASE_URL` (required) | PostgreSQL DSN. Apply the schema first: `python -m alembic upgrade head` |
| `INTERCEPTOR_PSEUDO_KEY` (required, 16 bytes or more) | Secret for patient and encounter pseudo-IDs |
| `INTERCEPTOR_JWT_PUBLIC_KEY_FILE` | PEM public key for bearer tokens. Unset: every token is refused |
| `INTERCEPTOR_MTLS_REGISTRY` | JSON file of registered client certificates. Unset: every clinical request is refused |
| `INTERCEPTOR_TRUSTED_PROXIES`, `INTERCEPTOR_PROXY_SECRET` | Address of the reverse proxy and the shared secret it sends |
| `INTERCEPTOR_UPSTREAM_BASE_URL`, `INTERCEPTOR_UPSTREAM_API_KEY`, `INTERCEPTOR_MODEL_ROUTES` | OpenAI-compatible upstream and the logical-to-provider model map (JSON). Unset: the proxy endpoint refuses |
| `INTERCEPTOR_RATE_LIMIT_PER_MINUTE` | Default 60 |
| `INTERCEPTOR_NLI_MODEL_PATH`, `INTERCEPTOR_NLI_MODEL_LOCK` | A local NLI model directory and a lock file. If set, the model must load, match the lock, and pass a warm-up check, or the service refuses to start |

### Run it for real (local)

```bash
bash scripts/gen_test_certs.sh                       # test CA, server, two client certificates, a JWT key pair
python -m alembic upgrade head                       # needs INTERCEPTOR_DATABASE_URL
python scripts/dev_server.py --port 8443             # TLS with a required client certificate
python scripts/smoke.py --base https://127.0.0.1:8443 --pseudo-key <the server's key>
```

The smoke test checks that a connection with no certificate is refused at the TLS handshake, that a CA-signed but unregistered certificate gets 403, and then walks decompose, verify, audit and both reads. It passed against a migrated PostgreSQL 18 database.

### Demo on Vercel

The service and the dashboard run on Vercel as two projects (a Python function and a Next.js app), on the
`vercel-demo` profile: bearer-only auth, synthetic data, and no NLI model. See
[docs/deployment.md](docs/deployment.md) for the environment variables, the database, and what the demo can and
cannot do.

### How the proxy behaves

- **Before the upstream is called:** the request is validated, the bundle is parsed, the patient must match, and the model must be in the routing table. A request that fails any of these is never forwarded. The FHIR bundle and the interception policy are never sent upstream.
- **Non-streaming:** all output is verified, the release policy is applied, and the audit record is written before the response is returned. If the audit write fails, the answer is `503 AUDIT_WRITE_FAILED` with no content.
- **Streaming:** each sentence is verified and its audit rows are written before it is released. A verdict event always comes before the text it covers. A slow sentence is withheld as `[WITHHELD: VERIFICATION_TIMEOUT]` and the stream continues. A verifier or audit failure ends the stream with an `error` event and `[DONE]`, and never a `finish_reason: "stop"`. Upstream errors before the first byte are ordinary JSON errors.
- **Anything that cannot be verified** is blocked. Without the NLI model that includes most free text, so a real model's prose will usually come back `BLOCKED`. This is the fail-closed design, not a bug, and it is the main limit of this build.

### Decisions still open

1. **HTTP trailers (Gate G4): resolved.** Latency values are not trailers. Streams carry `interception_time_ms` and `first_release_ms` in `stream.complete`; non-streaming responses carry `X-Interception-Time-MS` as a header. Approved 2026-10-20; see `docs/api-changes/2026-10-20-latency-trailers.md`.
2. **`BLOCK_RESPONSE` mid-stream.** API.md does not say what it means once earlier sentences are already released. This build stops the stream, sends `stream.complete` with status `BLOCKED` and `finish_reason: content_filter`, and keeps what was already released (it was audited).
3. **The NLI model (Gate G1).** The loader is built (`nli_service/scorer.py`): it reads the label order from the model's own config, refuses anything but entailment, neutral and contradiction, checks the directory hash against a lock file, and runs a warm-up check. It needs `sentence-transformers` and a checkpoint. The checkpoint `cross-encoder/nli-deberta-v3-base` is approved and locked in `nli_service/MODEL.lock` (the weights are downloaded locally, never committed). Without a configured model, claims that need it fail closed.
4. **Per-client authorization.** API.md says reads return 404 for a patient the client may not see, but does not define which client may see which patient. Today any authenticated client can read any note or report.
5. **Layout, preflight and tooling.** The code is in `src/interceptor/` (modules `core/`, `api/`, `fhir/`, `nli/`, `store/`, `intercept/`, not the plan's exact tree). Docker Compose, ruff, mypy and CI are set up; pre-commit is not.

### Choices made without a schema change

DATABASE.md has no table for reports or idempotency keys, and its payload rule allows only IDs and codes. So:

- **Idempotency keys** are stored as a hash of (client, key) plus a hash of the body inside trace payloads, and found by JSON containment. A key replayed with the same body returns the original result. A different body is `409`. Keys expire after 24 hours. A key used only on a duplicate note (nothing new written) is not remembered.
- **Reports** are rebuilt from the ATTRIBUTE trace payload and the linked events.
- **Client-written explanations** on attribution spans are validated but not stored, because they are free text. Responses carry server-generated explanations from a fixed template, so the text differs from API.md's examples.
- **Notes** return an index of claim IDs and offsets, not claim text, because claim text is not stored.
- **Streaming claim IDs** come from a per-stream placeholder hash (the final text is not known yet), so they do not match IDs derived from the final note hash. The note row gets its real hash when the stream ends, unless the same text was already audited for that patient.

### Conflicts and choices in API.md (please review)

- **401 vs 403 for a missing token:** section 10 says 403, the error table says 401. This build uses 401 for a missing, expired or badly signed token, and 403 for a missing scope or certificate.
- **Retryable column:** garbled in the PDF extraction. Values follow what each code means.
- **Examples that cannot work as written** (each pinned by a test in `tests/contract`): the example bundles have a Patient with no identifier, so the pseudo-ID cannot be checked (refused with `INTERCEPT_BUNDLE_INVALID`); the audit example's claim IDs and offsets do not match any real note (refused with `AUDIT_CLAIM_MISMATCH`); and the redacted spans in 4.1.3 (`[0,37]`, `[64,106]`) do not match the text (the real offsets are `[0,36]` and `[63,108]`).
- **Streaming order:** the 4.2 example shows a sentence's text before its verdict event for one sentence, which breaks rule 1 in 4.3. This build always sends the verdict first.
- **A withheld sentence** is reported as `risk_severity: CRITICAL`, `action: REDACTED`, because the event's action and severity sets are closed and have no value for "timed out".
- **Category table (7.1.2):** `TEMPORAL_MISMATCH` is not evaluated (the audit request has no date for the matched fact), and "entity is a diagnosis" is approximated as a SNOMED CT entity that is not an allergy concept.
- **Token and certificate are not bound:** the token's `sub` need not equal the certificate's client ID. Both must be valid, and the rate limit is per certificate.
- **Limits:** the 2 MiB check reads the `Content-Length` header only, and the rate limiter is in memory per worker.
- **Policy override on `/verify`:** `min_confidence_entailment` is accepted. It is applied in the proxy, but has no effect on `/verify` while the verifier's confidence is always 1.0.
- **Non-spec warning code:** `INTERCEPT_CLAIM_SKIPPED` marks a sentence that produced no usable claim.

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
- **Layout:** the code is in `src/interceptor/` as the plan asks. Module paths in this README are relative to that package.
- **Tooling:** `python scripts/check.py` runs ruff, mypy and the tests. Deviations from the plan: line length is 120 (not 100); ruff ignores E501, B904, B905 and UP042 for now (long lines, exception chaining, `zip(strict=)`, `StrEnum`), and the code is not formatted with `ruff format` (`--format` checks it); mypy runs in default mode over `src/` and is clean. `mypy --strict` is clean on `src/interceptor/core` and runs in CI; `store/` and `fhir/` are followed silently until they are annotated. `docker-compose.yml` was run end to end (PostgreSQL 18, migrations, API health) on 2026-10-20 and 2026-10-21.
