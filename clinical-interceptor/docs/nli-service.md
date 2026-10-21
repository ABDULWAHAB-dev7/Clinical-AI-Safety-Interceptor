# The NLI model and the inference service

The verifier needs a natural-language-inference model for the claims the deterministic rules cannot settle. This
document says which model, where it runs, and how it is wired in.

## The model

| | |
|---|---|
| Checkpoint | `cross-encoder/nli-deberta-v3-base` |
| Licence | Apache-2.0 (model card) |
| Lock | `models/nli/MODEL.lock`, SHA-256 `435ef41d…` |
| Parameters | 184,424,451 |
| Measured on CPU | batch 1: p50 149.8 ms; batch 4: p50 299.3 ms; batch 8: p50 508.7 ms |

Gate G1 approved this checkpoint; see [G1-nli-model.md](gates/G1-nli-model.md) for the selection memo and
[G1-measurements.json](gates/G1-measurements.json) for the latency numbers. The checkpoint itself is **not** in the
repository. `models/nli/*/` is in `.gitignore`, and the weights are about 6 GB.

### The lock file

`models/nli/MODEL.lock` holds the SHA-256 of the **model directory**: every file's relative path and content
hash, in a stable order. It is checked when the scorer loads, so a service that cannot prove it is running the
approved weights refuses to start.

The hash covers the whole `snapshot_download` output for the repository, which is 43 files: the safetensors
weights, the tokenizer, `config.json`, the README, the PyTorch `pytorch_model.bin`, the ONNX exports, and the
`.cache/huggingface` download metadata. Re-create the directory with the same tool and the hash reproduces:

```bash
huggingface-cli download cross-encoder/nli-deberta-v3-base \
    --local-dir models/nli/cross-encoder-nli-deberta-v3-base
```

A directory that differs by even one byte fails the check. That is the point: it is a supply-chain check, not a
version marker.

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

A serverless function cannot hold torch and a 6 GB checkpoint. Vercel's function limit is 250 MB, and torch on
its own is about 200 MB before the weights. So a deployment that runs the interceptor as a function needs the
model somewhere else.

The service is **part of the deployment, not a third party**. The premise is verbalised from the patient's record
and the hypothesis is text from the note; both stay inside the operator's own network. The Gate G1 memo rules out
a *hosted endpoint operated by someone else* ("a hosted endpoint would send the FHIR-derived premise text and
the claim to a third party on every NLI call. The plan does not allow that path"), and a service the operator
runs and controls is not that.

The verifier cannot tell the two apart: `RemoteNliScorer` satisfies the same protocol as the local
`NLIScorer`, so `core/verify.py` is unchanged and there is one implementation of the rules.

## Running the service

### With Docker Compose

```bash
export NLI_MODEL_DIR=/path/to/the/directory/that/holds/models
docker compose --profile nli up --build
```

The checkpoint is mounted, not baked into the image. `NLI_MODEL_DIR` must contain
`nli/cross-encoder-nli-deberta-v3-base/` and `nli/MODEL.lock`.

### On its own

```bash
pip install -r requirements-nli.txt
export NLI_MODEL_PATH=models/nli/cross-encoder-nli-deberta-v3-base
export NLI_MODEL_LOCK=models/nli/MODEL.lock
export PYTHONPATH=src
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
claims in a note, not for each one, and one forward pass over a batch costs less per pair than one pass per pair
(149.8 ms for a single pair against 74.8 ms per pair at batch size 4). Measured over a six-claim note through the
remote service: 498 ms one call per claim against 264 ms batched, with identical results.

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
