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

These came out of the first full run. They are not fixed in this milestone, because each fix changes verifier behaviour.

1. **Negation is scoped to the whole sentence.** "A: Type 2 diabetes mellitus, not at goal" (case 01, a clean statement)
   was blocked as a denial of diabetes. The `not` belongs to "at goal", but the verifier reads it as negating the
   diagnosis. This is a false block on a correct statement.
2. **A high-confidence NLI contradiction blocks an unrelated statement.** "Tension-type headache, most likely" (case 05)
   was blocked. The NLI model returned CONTRADICTION against the active diabetes triple, and because the triple is an
   active diagnosis, the severity rule made it CRITICAL. The same kind of false contradiction was seen earlier with
   "Blood pressure is stable."
3. **Every unsupported statement is a WARNING.** 70 of 87 clean claims carry `unsupported_clinical`, because the chart has
   no entry for them. Flagging is therefore almost all noise. This follows the rule that NEUTRAL gives a WARNING, but
   it means a clinician would see a warning on most sentences.
4. **Medication statements are always NEUTRAL.** The FHIR subset has no medication facts, so every medication sentence is
   flagged, including correct ones.

## Not evaluated

Cephalosporin cross-reactivity, laboratory critical values, and dose checks for drugs without a ceiling in
`knowledge/dose_limits.yaml`. See the report for why each is excluded.
