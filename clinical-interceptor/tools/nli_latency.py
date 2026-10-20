"""CPU latency and label-order check for candidate NLI cross-encoders (PLAN 4.1, Gate G1). Measurement only: it does not
choose a model.

    python tools/nli_latency.py --model cross-encoder/nli-deberta-v3-base --out docs/gates/G1-measurements.json

Reports the label order from the model config (PLAN 4.1 step 3), parameter count, and p50 / p95 per-batch latency on
CPU for batch sizes 1, 4 and 8, with premise and hypothesis lengths close to the verifier's inputs.
"""

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

PREMISE = (
    "Condition: Diabetes mellitus type 2, active, confirmed, onset 2019-05-02. "
    "AllergyIntolerance: Allergy to penicillin, active, confirmed, criticality high. "
    "Observation: Hemoglobin A1c 8.2 % on 2026-09-02."
)
HYPOTHESIS = "Patient denies known drug allergies."
ITERATIONS = 30
WARMUP = 3


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, round(fraction * len(ordered)) - 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import torch
    from sentence_transformers import CrossEncoder

    torch.set_num_threads(args.threads)
    started = time.perf_counter()
    model = CrossEncoder(args.model, device="cpu")
    load_s = time.perf_counter() - started

    config = model.model.config
    parameters = sum(p.numel() for p in model.model.parameters())
    result = {
        "model": args.model,
        "label_order": {int(k): v for k, v in config.id2label.items()},
        "parameters": parameters,
        "load_seconds": round(load_s, 2),
        "threads": args.threads,
        "python": platform.python_version(),
        "torch": torch.__version__,
        "cpu": platform.processor(),
        "batches": {},
    }
    for batch in (1, 4, 8):
        pairs = [(PREMISE, HYPOTHESIS)] * batch
        for _ in range(WARMUP):
            model.predict(pairs, batch_size=batch)
        timings = []
        for _ in range(ITERATIONS):
            t0 = time.perf_counter()
            model.predict(pairs, batch_size=batch)
            timings.append((time.perf_counter() - t0) * 1000)
        result["batches"][batch] = {
            "p50_ms": round(statistics.median(timings), 1),
            "p95_ms": round(percentile(timings, 0.95), 1),
            "per_pair_p50_ms": round(statistics.median(timings) / batch, 1),
        }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(Path(args.out).read_text(encoding="utf-8")) if Path(args.out).exists() else []
    existing = [e for e in existing if e["model"] != args.model] + [result]
    Path(args.out).write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
