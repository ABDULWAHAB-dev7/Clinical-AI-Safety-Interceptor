# The NLI model and the inference service

The verifier needs a natural-language-inference model for the claims the deterministic rules cannot settle. This
document says which model, where it runs, and how it is wired in.

## The model

| | |
|---|---|
| Checkpoint | `cross-encoder/nli-deberta-v3-base` |
| Revision | `6c749ce3425cd33b46d187e45b92bbf96ee12ec7` |
| Licence | Apache-2.0 (model card) |
| Lock | `nli_service/MODEL.lock`, a per-file manifest |
| Parameters | 184,424,451 |
| Measured on CPU | batch 1: p50 149.8 ms; batch 4: p50 299.3 ms; batch 8: p50 508.7 ms |

Gate G1 approved this checkpoint; see [G1-nli-model.md](gates/G1-nli-model.md) for the selection memo and
[G1-measurements.json](gates/G1-measurements.json) for the latency numbers. The checkpoint itself is **not** in the
repository. `nli_service/model/` is in `.gitignore`, and the weights are about 750 MB.

### The lock file

`nli_service/MODEL.lock` names the repository, the revision, and every file that makes up the model with its
SHA-256:

```json
{
  "model": "cross-encoder/nli-deberta-v3-base",
  "revision": "6c749ce3425cd33b46d187e45b92bbf96ee12ec7",
  "license": "apache-2.0 (model card)",
  "files": { "config.json": "...", "model.safetensors": "...", "tokenizer.json": "..." }
}
```

The check happens when the scorer loads: every named file must be present and hash to its digest, or the service
refuses to start.

Only the files the service actually loads are pinned: the weights, the tokenizer and the config, seven files and
about 750 MB. The exported ONNX graphs, the second copy of the weights in `pytorch_model.bin`, the README and the
download tool's `.cache` directory are not, for two reasons. The service never loads them, and the download tool's
bookkeeping holds the time of the download, so pinning it would make the lock match only the one directory it was
made from.

An earlier version of the lock hashed the whole download directory and was therefore neither reproducible nor
portable: it verified on the machine that produced it and failed on the Linux host. The manifest fixes both.

### Fetching the checkpoint

The lock drives the download, so the same bytes arrive everywhere:

```bash
python -m nli_service.fetch_model --out nli_service/model/cross-encoder-nli-deberta-v3-base
```

The tool fetches exactly the files the lock names, at the revision the lock names, and then checks them. A file
that does not match stops it. `tools/write_nli_lock.py` writes the manifest when the model is re-approved.

### Reading the label order

The scorer never assumes the label order. It reads `id2label` from the model's own `config.json` and refuses
anything that is not exactly `entailment`, `neutral` and `contradiction`. The approved checkpoint happens to use
`{0: contradiction, 1: entailment, 2: neutral}`, which is the reverse of the other candidate model, so assuming
an order would silently invert every verdict.

## The three modes

| `INTERCEPTOR_NLI_MODE` | What happens |
|---|---|
| `none` (default) | No model. A claim the rules cannot settle raises `VERIFICATION_ERROR`, which is hard-stop rule R2, so the document is blocked and nothing is released. |
| `local` | The checkpoint is loaded in the interceptor's own process. Needs `INTERCEPTOR_NLI_MODEL_PATH` and torch in the image. |
| `remote` | The interceptor calls a self-hosted inference service at `INTERCEPTOR_NLI_ENDPOINT`. |

The mode is also inferred: if `INTERCEPTOR_NLI_MODEL_PATH` is set and no mode is given, the mode is `local`.

### Why a separate service exists

A serverless function cannot hold torch and a 750 MB checkpoint. Vercel's function limit is 250 MB, and torch on
its own is about 200 MB before the weights. So a deployment that runs the interceptor as a function needs the
model somewhere else.

The service is **part of the deployment, not a third party**. The premise is verbalised from the patient's record
and the hypothesis is text from the note; both stay inside the operator's own network. The Gate G1 memo rules out
a *hosted endpoint operated by someone else* ("a hosted endpoint would send the FHIR-derived premise text and the
claim to a third party on every NLI call. The plan does not allow that path"), and a service the operator runs and
controls is not that.

The verifier cannot tell the two apart: `RemoteNliScorer` satisfies the same protocol as the local scorer, so
`core/verify.py` is unchanged and there is one implementation of the rules.

## The service package

`nli_service/` is self-contained on purpose. The inference service is built as a container whose build context is
that directory, so it cannot import from the interceptor package, and the two must not drift apart:

```
nli_service/
  app.py           the FastAPI service
  scorer.py        the local scorer: loads the checkpoint, checks the lock
  errors.py        the error type the service raises
  fetch_model.py   fetches the approved checkpoint at its locked revision
  MODEL.lock       the approved artefact: every file with its digest
  Dockerfile.vercel
  requirements-nli.txt
```

The interceptor uses the same scorer through `interceptor.nli.scorer`, which re-exports it and translates the
service's error type into the interceptor's taxonomy.

## Running the service

### On Vercel

The service is a Vercel project of its own, `clinical-interceptor-nli`, built from `nli_service/Dockerfile.vercel`
and declared as a service in the repository's `vercel.json`. The image bakes the approved checkpoint in and the
build fails if a byte does not match the lock, so what runs is a checkpoint that can be checked rather than one
that has to be trusted.

The interceptor reaches it at `https://clinical-interceptor-nli.vercel.app` with
`INTERCEPTOR_NLI_MODE=remote`.

A container function scales to zero when idle, and booting the model takes longer than one scoring call, so
`INTERCEPTOR_NLI_TIMEOUT_SECONDS` sets the budget for an NLI call, retries included, and the client retries a
transport failure rather than giving up at the first refusal. A protocol failure is not retried: that is an
answer, and an answer must not be second-guessed.

### With Docker Compose

```bash
docker compose --profile nli up --build
```

### On its own

```bash
pip install -r nli_service/requirements-nli.txt
export NLI_MODEL_PATH=nli_service/model/cross-encoder-nli-deberta-v3-base
export NLI_MODEL_LOCK=nli_service/MODEL.lock
export PYTHONPATH=.
python -m uvicorn nli_service.app:app --host 127.0.0.1 --port 8100
```

The service loads the checkpoint at start-up. If it cannot, it does not start: a service that is up but cannot
answer is worse than one that is down.

| Variable | Meaning |
|---|---|
| `NLI_MODEL_PATH` | The checkpoint directory. Required. |
| `NLI_MODEL_LOCK` | The lock file. Optional, but without it the weights are not checked. |
| `NLI_SERVICE_TOKEN` | If set, `POST /v1/score` needs `Authorization: Bearer <token>`. |
| `NLI_MAX_PAIRS` | The most pairs in one request. Default 64. |
| `NLI_MAX_TEXT_CHARS` | The most characters in a premise or a hypothesis. Default 4000. |
| `OMP_NUM_THREADS` | Torch threads. The default in the image is 4. |

### Endpoints

```
GET  /v1/health   -> {"status": "ok", "model": ..., "lock_verified": true, "labels": [...]}
POST /v1/score    {"pairs": [["premise", "hypothesis"], ...]}
                 -> {"scores": [{"entailment": ..., "neutral": ..., "contradiction": ...}, ...]}
```

`POST /v1/score` answers 503 and no scores if the model fails, so the caller has nothing to fall back on.

The premise and the hypothesis are clinical text. The service does not log them.

## Batching

The verifier scores every claim that needs the model in **one** call. The latency budget is for all NLI-routed
claims in a note, and one forward pass over a batch costs less per pair than one pass per pair (149.8 ms for a
single pair against 74.8 ms per pair at batch size 4). Measured over a six-claim note through the remote service:
498 ms one call per claim against 264 ms batched, with identical results.

A scorer without a `score_batch` method still works: the verifier falls back to one call per pair.

## Failing closed

The remote scorer raises on every failure: the service is unreachable, the answer is not HTTP 200, the body is
not JSON, the number of scores is wrong, a label is missing, a score is outside 0 to 1. `core/verify.py` turns
any of those into a `VerificationError`, which is rule R2, so the document is blocked and nothing is released.

There is no retry with a different answer, and no fallback to a default score. A claim that cannot be scored is
not a claim that is fine.

## What this does not settle

- **The model is not clinically validated.** It is trained on SNLI, MultiNLI and FEVER, not on clinical text. Its
  behaviour on clinical statements is unknown beyond the benchmarks in this repository.
- **The NLI class never decides the severity on its own.** `core/severity.py` sets the severity, and a
  contradiction only reaches `CRITICAL` when it was matched by a code, an allergy concept or a drug class. A
  contradiction found on shared words alone stays a `WARNING`.
- **Thresholds are not calibrated.** `knowledge/thresholds.yaml` says so, and the policy-change records have no
  clinical reviewer assigned.
