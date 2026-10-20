# Real-note evaluation (public MTSamples transcriptions)

This is the first measurement on notes written as clinicians dictate them, not on the templates used to build the rules.
It is a small, limited evaluation. Read the caveats before quoting any number.

## Data

- 20 transcriptions from mtsamples.com: 8 discharge summaries, 5 SOAP or progress notes, 5 general medicine consults and
  histories, and 2 cardiology echocardiogram reports. The list with URLs is in `results/sources.json`.
- The note text is **not** in this repository. The site allows educational sharing with attribution, and it describes
  its samples as reference material. The script reads the notes from a folder you supply.
- The site says names and dates were changed or removed. That is the site's claim, and it has not been checked.

## Method

`run_realnotes.py` builds two documents per note:

- **Clean:** the note as written.
- **Injected:** the same note with labelled errors appended as their own lines.

Each document goes through decompose, verify and audit in process, as in the other benchmarks.

The FHIR record is synthetic, and it is built by a script from facts the note itself states that the verifier can encode:
type 2 diabetes (a Condition) and an HbA1c value (an Observation). Nobody has checked it against a real chart, so it is
not an independent ground truth.

Errors are injected only where the record can check them:

| Error | Expected | Scored |
|---|---|---|
| Fabricated history ("History of stroke in 2015.") | WARNING | Yes, on all 20 notes |
| Inverted HbA1c (a value far from the record) | WARNING | Only where the note gives HbA1c as a percentage. None of the 20 notes does. |
| Dosing-frequency error | needs a record that states the medication | No |
| Beta-lactam cross-reactivity | needs a documented penicillin allergy | No |
| Allergy denial | needs a documented allergy | No |

The last three cannot be checked against a record built from these notes. Only a record that independently states the
fact could check them.

## Results (`results/metrics.json`)

| Measure | Value | Meaning |
|---|---|---|
| Blocking, document level | TP 0, FP 0, FN 20 | No injected document was blocked by a contradiction. Precision is undefined and recall is 0. |
| Flagging, claim level | TP 19, FP 2,356, FN 0 | 19 of 20 fabricated statements were flagged as WARNING. Precision 0.8%, F1 1.6%. |
| False-positive blocks | 0 of 20 clean documents | No clean document was blocked by a contradiction. |
| Fail-closed | 1 clean, 1 injected | Blocked because a statement needs the NLI model, which is not active. |

The flagging result matters most. The system flags almost every statement it cannot find in the record as "unsupported".
A fabricated history and an ordinary unsupported statement look the same to it. So a flag does not indicate a fabrication.

## Caveats

1. **Not an independent ground truth.** The record is derived from the note's own statements by a script. A real check
   needs a clinician-reviewed record for each note.
2. **Small sample.** Twenty notes, one of which is only 738 characters, and two injected error types, one of them scored
   on all 20 notes.
3. **Narrow knowledge.** The verifier knows one diagnosis, one lab and a handful of drugs. Most statements in these notes
   are outside its knowledge, so the blocking results mostly show what it cannot see.
4. **Gate G2 is not involved.** MTSamples is not MIMIC-IV, so no PhysioNet agreement applies. The terms above still apply.

## Run

```bash
python -m benchmark.realnotes.run_realnotes --notes <folder with note_NN.txt and manifest.json> --out benchmark/realnotes/results
```
