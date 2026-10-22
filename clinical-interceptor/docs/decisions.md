# Decisions that are not engineering decisions

These need a named human owner. The code has a working default for each so the system runs, but a default is not an
approval. Nothing here is a regulatory or clinical claim. Update the "Owner" and "Decision" columns as people decide.

| # | Question | What the code does today | Owner | Decision |
|---|---|---|---|---|
| 1 | Lab tolerance, unit conversion and critical-value limits: which source is authoritative? | Tolerance is a placeholder; units must match exactly; limits in `knowledge/` each carry a source note | Clinical lead | open |
| 2 | Override vocabulary and who may override | Any holder of `interceptor:override`; reason stored as a hash; reviewer is a pseudonym of a self-asserted signature | Clinical safety officer | open |
| 3 | Who may acknowledge a warning | Any holder of `interceptor:acknowledge`; same pseudonym rule as above | Clinical safety officer | open |
| 4 | Audit retention period | 6 years assumed; `scripts/retention.py` reports and exports, never deletes | Compliance | open |
| 5 | Regulatory pathway (is this a medical device?) | Not claimed anywhere. `docs/VALIDATION_REPORT.md` is a pilot measurement, not a validation | Regulatory | open |
| 6 | Review store design (where reviews of full text would live) | Reviews hold a resolution, a pseudonym and a hash. No note text is stored | Architecture and compliance | open |
| 7 | BAA coverage for the upstream model used by the proxy | The proxy exists. Do not send real patient data to it until a BAA, or a local model, is in place | Compliance | open |
| 8 | NLI model licence | `cross-encoder/nli-deberta-v3-base` approved (Gate G1, see `docs/gates/G1-nli-model.md`); weights are never committed | Legal | approved for evaluation |
| 9 | PRD section 5 and FR-5.x text | Not in the copy the build used. Rules follow ARCHITECTURE 7.2 and 3.7 | Product | open |
| 10 | Bearer-only demo auth versus mTLS production | `INTERCEPTOR_AUTH_MODE=bearer` only runs with `INTERCEPTOR_DEPLOYMENT_PROFILE=vercel-demo`. Production uses both factors | Security | demo approved, production open |
| 11 | Scope of R3 (any contradiction blocks) | Contradiction blocks only for high-risk kinds with confidence above 0.80 (`docs/policy-changes/2026-10-20-nli-critical-threshold.md`, `2026-10-21-negation-nli-scope.md`) | Clinical lead | open |
| 12 | When verification is unavailable | Fail closed: nothing is released, the note is stored as `FAILED`, the safety team is notified | Clinical safety officer | open |
| 13 | Notification receiver | The webhook posts IDs only. Where it goes, and who answers it, is not decided | Operations | open |
| 14 | Who holds the pseudo-ID key and the token signing keys | Environment secrets today. A key manager is expected for production | Security | open |
