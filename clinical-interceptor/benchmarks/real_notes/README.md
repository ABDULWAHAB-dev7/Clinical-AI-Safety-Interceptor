# Real-note benchmark (Milestone 3.2)

Ten synthetic clinical notes, each with its own FHIR chart and some seeded errors, scored against the verifier.
The result is in [`docs/VALIDATION_REPORT.md`](../../docs/VALIDATION_REPORT.md).

```bash
python benchmarks/real_notes/build_charts.py   # writes cases/case-NN/bundle.json from the chart definitions
python benchmarks/real_notes/run.py            # verifies every note with the NLI model; writes results and the report
```

## Layout

| Path | Contents |
|---|---|
| `build_charts.py` | The three charts (A, B, C) and the case-to-chart map. Charts are written before the notes and are not derived from them. |
| `cases/case-NN/note.txt` | The note, written for this benchmark in SOAP, discharge, clinic, urgent-care or consult style. |
| `cases/case-NN/bundle.json` | The FHIR R4 chart the verifier checks the note against. |
| `cases/case-NN/gold.json` | The seeded errors: the exact sentence, the error kind, the chart fact it contradicts, and the expected severity. Any other sentence is clean. |
| `run.py` | Decomposes each note, verifies each claim (`verify_claim` with the NLI model), and scores the result. |
| `results/results.json` | Per-claim severities, rules and NLI classes for every case. Synthetic text only. |

## Method

1. Each note is decomposed into claims with `decompose_claims`, the function the API uses.
2. Each claim is verified against its chart's triples with `verify_claim`, using the lock-checked NLI model.
3. A claim that raises `VerificationError` counts as failed closed, which the API treats as a refused request.
4. A claim is *inside* a seeded error if it starts inside the seeded sentence. It is *flagged* if its severity is WARNING or
   CRITICAL, and *blocked* if it is CRITICAL.

The runner calls the core functions directly. It does not go through the HTTP routes, so it needs no database and no
certificates.

## Findings

The first full run found two false blocks. Both are fixed in `core/verify.py` and covered by
`tests/unit/test_verify_scope.py`. The change is recorded in `docs/policy-changes/2026-10-21-negation-nli-scope.md`.

1. **Negation was scoped to the whole sentence (fixed).** "A: Type 2 diabetes mellitus, not at goal" (case 01, a clean
   statement) was blocked as a denial of diabetes. A negation cue now governs an entity only when it comes before the
   entity in the same clause.
2. **A shared-word NLI contradiction blocked an unrelated statement (fixed).** "Tension-type headache, most likely"
   (case 05) was blocked, because the NLI model returned a high-confidence contradiction against the diabetes entry, and
   the severity rule escalated it because the entry is an active diagnosis. A contradiction now escalates by kind only
   when the candidate was matched by a code, an allergy concept, or a drug class.
3. **Every unsupported statement is a WARNING (open).** About 70 of 87 clean claims carry `unsupported_clinical`, because
   the chart has no entry for them. This follows the rule that NEUTRAL gives a WARNING. It is not changed, but it means a
   clinician would see a warning on most sentences.
4. **Medication statements are always NEUTRAL (open).** The FHIR subset has no medication facts, so every medication
   sentence is flagged, including correct ones.

After the fixes, blocking has no false blocks on this set (4 of 4 seeded blocks caught), and all 8 seeded errors are still
caught at WARNING or CRITICAL as before. The sample is 10 notes, so this is not evidence of accuracy beyond this set.

## Not evaluated

Cephalosporin cross-reactivity, laboratory critical values, and dose checks for drugs without a ceiling in
`knowledge/dose_limits.yaml`. See the report for why each is excluded.
