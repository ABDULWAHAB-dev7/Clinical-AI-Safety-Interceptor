# Gate G1: NLI model selection

Status: **approved by the project owner** for `cross-encoder/nli-deberta-v3-base` (lock in `models/nli/MODEL.lock`). The text below is the original memo, kept for the record. PLAN 4.1 says candidates go to the human reviewer with licence, parameter
count and CPU latency, and that the choice is not made on the assistant's judgment. This memo gives the constraints and
the candidates. The CPU latency numbers are not measured yet (see "Needed to close G1").

## Constraints

| Constraint | Value | Source |
| --- | --- | --- |
| Where inference runs | Local, CPU, through `sentence_transformers.CrossEncoder(path, device="cpu")` | PLAN 4.1 step 4 |
| Model type | Cross-encoder. A bi-encoder embeds premise and hypothesis separately, so it cannot output entailment, neutral or contradiction for a pair. | Technical requirement of NLI |
| NLI verify budget | 350 ms p95 for all NLI-routed assertions in a note | ARCHITECTURE 7.3 |
| `verify`, 20 claims | 150 ms p95, request receipt to commit | API 9 (proposed) |
| End-to-end | 800 ms p95, excluding upstream generation | ARCHITECTURE 7.3, NFR-1.1 |
| Per-call timeout | 5 s; a timeout fails closed | `knowledge/thresholds.yaml` |
| Throughput | **Not specified.** The architecture mentions "sustained throughput" but gives no number. | ARCHITECTURE section 10 (queueing) |
| Hardware available here | AMD Ryzen 7 5825U, 8 cores / 16 threads, 15.3 GB RAM, no NVIDIA GPU (integrated Radeon, 512 MB shared) | Measured on this machine |
| Production hardware | Unknown | Needs your answer |

Deterministic-first routing already keeps NLI for claims the rules cannot settle (coded matches, medication and dose
claims never call the model). The number of NLI calls per note is the figure to size against the budget. It is not
measured yet either.

## Local or hosted

Local is the plan's default. A hosted endpoint would send the FHIR-derived premise text and the claim to a third party
on every NLI call. The plan does not allow that path, and it would need its own privacy review. This memo does not
recommend a hosted endpoint.

## Candidates

Licence and size figures come from search results and model cards. They are not confirmed against the model files.

| Candidate | Licence (as found) | Parameters | Training data | Notes |
| --- | --- | --- | --- | --- |
| `cross-encoder/nli-deberta-v3-base` | Apache-2.0 (model card metadata) | about 184 M (DeBERTa-v3-base size, to confirm) | SNLI, MultiNLI | Smallest of the candidates. Base DeBERTa licence not checked. |
| `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` | MIT (Hugging Face tag) | about 184 M (to confirm) | MultiNLI, FEVER-NLI, ANLI (about 764 k pairs) | Same size class as the first, more training data. Model card licence section not read in full. |
| `MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli` | not checked | about 435 M (to confirm) | larger set, not checked | Reference only: the author recommends it for accuracy over speed. Likely too slow for the 350 ms budget on this CPU. |

Licence terms for the training datasets (SNLI, MultiNLI, FEVER, ANLI) were not found in the searches. They need checking
before approval. So does the base model's own licence.

**Clinical domain:** none of these models is trained on clinical text. Their accuracy on clinical statements is unknown.
The synthetic benchmark cannot measure it, because no benchmark claim reaches the NLI model (BG1 is "not evaluable").
Validating NLI on clinical text needs either approved real data (Gate G2) or a clinically reviewed test set.

## Where it runs

The checkpoint does not fit in a serverless function, so it runs in a separate service that the operator hosts:
`nli_service/` in this repository, with `INTERCEPTOR_NLI_MODE=remote` on the interceptor. The premise and the
claim stay inside the operator's network, which is the constraint above; a hosted endpoint operated by a third
party is still not allowed. See [../nli-service.md](../nli-service.md).

The latency numbers above were measured with the checkpoint in the same process. Through the service, one note of
six claims takes 264 ms batched against 498 ms one call per claim, because the batch is one forward pass.

## Needed to close G1

1. **Permission to install** `torch` and `sentence-transformers` (CPU builds) and to **download** the two base candidates.
   The base checkpoints are several hundred MB each. The large reference is larger. Exact sizes are unconfirmed, so the
   download will be shown to you first. Nothing is downloaded until you say yes.
2. **Your throughput target:** requests per second, and the largest note and claim count you expect. Without it, a
   latency pass cannot say whether a model fits.
3. **Where the service will run,** so the latency numbers can be read against the right machine.
4. **The licence decision** on the approved checkpoint, and whether the training-data licences are acceptable.

Once 1 is granted, the measurement plan is: CPU latency at batch sizes 1, 4 and 8 with 128-token pairs, fp32 through
`sentence-transformers`, and int8 through `onnxruntime` (already installed here). Report p50 and p95 for each. The
choice is then yours.
