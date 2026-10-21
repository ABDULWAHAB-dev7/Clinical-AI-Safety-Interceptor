# Policy change: negation scope and NLI corroboration (2026-10-21)

Status: implemented on branch `main`. Clinical review: **none assigned**. This change was made by the project
maintainer after the real-note benchmark (`benchmarks/real_notes`, see `docs/VALIDATION_REPORT.md`) found two false
blocks on correct statements. It has not been reviewed by a clinician.

## What changed

1. **Negation scope** (`core/verify.py`, `_negation_governs`). A negation cue governs a coded entity only when the cue
   comes before the entity, in the same clause. Clause boundaries are `, ; : . ! ?`.
   - Before: a cue anywhere in the claim negated every entity in it. "Type 2 diabetes mellitus, not at goal" was read as
     a denial of the diagnosis.
   - After: that statement is ENTAILMENT. "No history of type 2 diabetes" is still CONTRADICTION.
2. **NLI corroboration** (`core/verify.py`, `_nli`). A contradiction keeps its high-risk kind (allergy,
   medication dose, active diagnosis) only when the candidate triple was matched by a code, an allergy concept, or a drug
   class. A candidate matched on shared words alone keeps its kind as an empty set, so the severity rule gives a WARNING
   (`contradiction`), not a CRITICAL block.

## Why

- Benchmark run before the change: 2 of 87 clean claims were CRITICAL. Both were false blocks (`case-01`, `case-05`).
- The verifier's NLI class is unchanged. Only the severity from a shared-word match is affected.

## Effect on blocking

- A CRITICAL now needs one of: a deterministic rule (allergy conflict, dose over a configured ceiling, or a coded
  condition denied), or a code-matched NLI contradiction in a high-risk class above the threshold in
  `knowledge/thresholds.yaml`.
- An NLI-only contradiction that shares words with the record can no longer block by itself.
- This narrows blocking. Some true contradictions that only the NLI model can see will now be WARNINGs instead of blocks.
  The benchmark has no such case, so the size of this effect is not measured.

## Not changed

- A verification error (the NLI model is missing or fails) still refuses the request. This is the fail-closed rule.
- `core/policy.py` `decide()` (hard-stop rules R1 to R4) is still not used by the request path. See `benchmarks/real_notes/README.md`.

## Tests

`tests/unit/test_verify_scope.py` covers both changes.
