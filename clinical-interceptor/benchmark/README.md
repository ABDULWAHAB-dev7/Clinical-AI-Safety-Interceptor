# Benchmark (PLAN phase 7)

Measures the verifier against an answer key on synthetic notes. Read the limits below before quoting any number.

## Data modes

| Mode | Status | Where the data lives |
| --- | --- | --- |
| Synthetic generator (default) | Built: `corpus/v1` | `benchmark/corpus/v1/`, committed, invented names and identifiers only |
| Real MIMIC-IV-Note | **Not used.** Gate G2 is not passed: no PhysioNet data use agreement is confirmed | Outside the repository, for example `$HOME/data/mimic-iv/`. Ignored by `.gitignore` (`data/`, `benchmark/corpus/mimic/`) |

If G2 is ever passed, real-data mode reads the text locally, writes only aggregate metrics to `benchmark/results/`, and
never writes note text into `seed/`, `benchmark/corpus/`, logs, or results.

## Commands

```bash
python -m benchmark.generator.generate --seed 20261019 --n-clean 200 --n-halluc 200 --out benchmark/corpus/v1
python -m benchmark.generator.validate --corpus benchmark/corpus/v1
python -m benchmark.runner --corpus benchmark/corpus/v1 --split dev --out benchmark/results
python -m benchmark.runner --corpus benchmark/corpus/v1 --split test --out benchmark/results
python -m benchmark.metrics --run benchmark/results/<run_id>
python -m unittest tests.unit.test_benchmark
```

Run them from the project root. Each run writes `records.jsonl` (one line per note), `env.json` (git SHA, thresholds and
policy hashes, corpus hash, package versions) and, after `metrics`, `metrics.json` with bootstrap intervals.

## What is in the corpus (v1)

- 400 notes: 200 clean and 200 hallucinated, from four synthetic patient contexts (allergy present or not, type 2
  diabetes present or not). Split 40% dev and 60% test, with a fixed seed.
- Clean notes contain only statements the patient's record supports. Hallucinated notes add 1 to 3 injected errors.
- Every statement has a paraphrase, chosen by the seeded RNG, so the verifier does not see one fixed sentence.
- Gold labels come from the template table in `generator/generate.py`, written by hand. They are not read back from the
  verifier.

## Limits, which matter more than the numbers

- **The corpus is small and was built around the verifier's known branches.** There are 3 grounded and 6 injected
  statement templates, with about 2 paraphrases each. The verifier matches every gold label on both splits. That shows
  the verifier still handles these templates. It does **not** estimate accuracy on real notes.
- **No claim in v1 needs the NLI model.** Free-text claims that need it fail closed (no model is approved, Gate G1).
  NLI accuracy and macro-F1 are therefore reported over deterministic branches only, and gate BG1 is marked
  `not_evaluable`.
- **The proxy path and the HTTP transport are not measured.** Only decompose, verify and audit run, in process.
- **Latency numbers come from an in-process client**, so they exclude TLS and the network.
- The notes are 1 to 3 sentences each. Real notes are longer and messier. The decomposer is rule-based and has not been
  tested on real text.

## Metrics and gates

Claims are matched by span containment: a gold statement pairs with the smallest predicted claim whose span contains it.
Exact-offset equality is reported next to containment. Bootstrap intervals use 1000 note-level resamples with seed 0.

Gates (PLAN 7.5). The plan's gate table is garbled in places, so only the readable gates are enforced:

| Gate | Metric | Threshold | Status |
| --- | --- | --- | --- |
| BG1 | NLI macro-F1 | >= 0.95 | not evaluable (no NLI claims in v1) |
| BG2 | CRITICAL recall, test split | >= 0.80 | enforced |
| BG3 | clean-note flag rate, test split | <= 0.05 | enforced. This is my reading of the table, to reconcile with PRD.md |
| BG6 to BG8 | latency budgets | 800, 250 and 400 ms | reported in `metrics.json`, not enforced: the plan's mapping of budgets to scopes is unclear |

## Known finding: category labels

API.md 7.1.2 rule 1 labels any CONTRADICTION that involves a drug in an allergy class as `DRUG_ALLERGY_CONFLICT`. It
does this even when the patient has no matching allergy. So "Started amoxicillin 1500 mg once", a dose above the limit
in a patient with no penicillin allergy, is labelled `DRUG_ALLERGY_CONFLICT`. The gold label in this corpus is
`MEDICATION_DOSE_MISMATCH`, which matches the clinical meaning. That disagreement is why `category_accuracy` is about
0.87, and every miss is of this kind. The severity and rule are correct. Whether the contract should change is a
decision for the project owner, so the code and the gold are both left as they are.
