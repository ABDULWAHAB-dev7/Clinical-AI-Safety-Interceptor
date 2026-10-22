"""Benchmark runner (PLAN 7.3). For each note in one split it calls decompose, verify and audit, in process.

    python -m benchmark.runner --corpus benchmark/corpus/v1 --split dev --out benchmark/results

Writes benchmark/results/<run_id>/records.jsonl (one line per note) and env.json (what the run was made with).
The proxy path and the HTTP transport are not in v1: the proxy needs a mock upstream, and the transport test (one full
pass over 20 notes over TLS) is still to do. No NLI model is configured, so a claim that needs one fails closed and the
note is recorded with its error code.
"""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from benchmark.generator.generate import corpus_digest  # noqa: E402
from interceptor import __version__ as PACKAGE_VERSION  # noqa: E402
from interceptor.api.app import create_app  # noqa: E402
from interceptor.api.auth import JwtVerifier  # noqa: E402
from interceptor.api.mtls import MtlsConfig  # noqa: E402
from interceptor.api.ratelimit import RateLimiter  # noqa: E402
from interceptor.core.pseudo import patient_pseudo_id  # noqa: E402
from interceptor.store.records import MemoryAuditStore  # noqa: E402

# Synthetic key for pseudonyms in the benchmark. It is not a secret and never leaves the benchmark.
BENCH_KEY = b"benchmark-pseudonym-key-0123456789ab"
SCOPES = "interceptor:decompose interceptor:verify interceptor:audit:write interceptor:read"
FINGERPRINT = hashlib.sha256(b"benchmark-client-certificate").hexdigest()
PROXY_SECRET = "benchmark-proxy-secret-0123456789abcdef"
CLAIM_FIELDS = ("claim_id", "claim_text", "char_start", "char_end", "entities")


class Client:
    """Calls the interceptor in process, with the same headers a trusted proxy would add."""

    def __init__(self):
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
        self.app = create_app(
            policy_path=ROOT / "config" / "policy" / "default.yaml",
            fhir_dir=ROOT / "seed" / "fhir",
            store=MemoryAuditStore(),
            pseudo_key=BENCH_KEY,
            verifier=JwtVerifier(public),
            nli_model=None,
            mtls=MtlsConfig({FINGERPRINT: "benchmark"}, frozenset({"testclient"}), PROXY_SECRET.encode("ascii"),
                            {"benchmark": None}),
            limiter=RateLimiter(10_000_000),
        )
        self.http = TestClient(self.app)
        now = int(time.time())
        token = jwt.encode({"sub": "benchmark", "aud": "clinical-interceptor", "iat": now, "exp": now + 600,
                            "scope": SCOPES}, private, algorithm="ES256")
        self.headers = {"Authorization": f"Bearer {token}", "X-Client-Cert-SHA256": FINGERPRINT,
                        "X-Proxy-Secret": PROXY_SECRET}

    def post(self, path, body, idempotent=False):
        """Returns (status, json body or None, elapsed milliseconds)."""
        extra = {"Idempotency-Key": str(uuid.uuid4())} if idempotent else {}
        started = time.perf_counter()
        response = self.http.post(path, json=body, headers=self.headers | extra)
        elapsed = (time.perf_counter() - started) * 1000
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response.status_code, payload, elapsed


def _error_code(payload):
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return payload["error"].get("code")
    return None


def run_note(client, note_id, label, context, text, gold, bundle, patient_id):
    record = {
        "note_id": note_id,
        "label": label,
        "patient_context": context,
        "gold": gold,
        "predicted": [],
        "events": [],
        "pipeline_status": None,
        "overall_risk_severity": None,
        "error": None,
        "http": {},
        "latency_ms": {},
        "trace_ids": {},
    }

    status, body, ms = client.post("/v1/interceptor/decompose", {
        "patient_pseudo_id": patient_id,
        "note": {"source_system": "BENCH", "note_type": "PROGRESS_NOTE", "text": text},
    }, idempotent=True)
    record["http"]["decompose"], record["latency_ms"]["decompose"] = status, round(ms, 3)
    if status >= 300:
        record["error"] = _error_code(body) or f"HTTP_{status}"
        return record
    note_id_api, claims = body["note_id"], body["claims"]
    record["trace_ids"]["decompose"] = body.get("decompose_trace_id")

    status, verified, ms = client.post("/v1/interceptor/verify", {
        "patient_pseudo_id": patient_id,
        "note_id": note_id_api,
        "fhir_bundle": bundle,
        "claims": [{k: c[k] for k in CLAIM_FIELDS} for c in claims],
    })
    record["http"]["verify"], record["latency_ms"]["verify"] = status, round(ms, 3)
    if status >= 300:
        record["error"] = _error_code(verified) or f"HTTP_{status}"
        return record
    record["trace_ids"]["verify"] = verified["verify_trace_id"]

    spans = {c["claim_id"]: c for c in claims}
    record["predicted"] = [
        {
            "claim_id": r["claim_id"],
            "claim_text": spans[r["claim_id"]]["claim_text"],
            "char_start": spans[r["claim_id"]]["char_start"],
            "char_end": spans[r["claim_id"]]["char_end"],
            "nli_class": r["nli_class"],
            "risk_severity": r["risk_severity"],
            "risk_rule": r["risk_rule"],
            "matched_fhir_ref": r["matched_fhir_ref"],
            "confidence": r["confidence"],
        }
        for r in verified["results"]
    ]

    status, report, ms = client.post("/v1/interceptor/audit", {
        "note_id": note_id_api,
        "patient_pseudo_id": patient_id,
        "verify_trace_ids": [verified["verify_trace_id"]],
        "claims": claims,
        "results": verified["results"],
    }, idempotent=True)
    record["http"]["audit"], record["latency_ms"]["audit"] = status, round(ms, 3)
    if status >= 300:
        record["error"] = _error_code(report) or f"HTTP_{status}"
        return record
    record["pipeline_status"] = report["pipeline_status"]
    record["overall_risk_severity"] = report["overall_risk_severity"]
    record["trace_ids"]["report"] = report["trace_id"]
    record["events"] = [
        {"claim_id": e["claim_id"], "category": e["category"], "risk_severity": e["risk_severity"],
         "risk_rule": e["risk_rule"]}
        for e in report["hallucination_events"]
    ]
    return record


def _git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def environment(corpus):
    lock = ROOT / "models" / "nli" / "MODEL.lock"
    packages = {}
    for name in ("fastapi", "pydantic", "psycopg", "pyjwt", "cryptography"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not installed"
    return {
        "git_sha": _git_sha(),
        "nli_model": lock.read_text(encoding="utf-8") if lock.exists() else "none configured",
        "thresholds_sha256": hashlib.sha256((ROOT / "knowledge" / "thresholds.yaml").read_bytes()).hexdigest(),
        "policy_sha256": hashlib.sha256((ROOT / "config" / "policy" / "default.yaml").read_bytes()).hexdigest(),
        "corpus_sha256": corpus_digest(corpus),
        "interceptor_version": PACKAGE_VERSION,
        "python": platform.python_version(),
        "packages": packages,
    }


def run(corpus, split, out):
    corpus = Path(corpus)
    splits = json.loads((corpus / "splits.json").read_text(encoding="utf-8"))
    ids = splits[split]
    client = Client()
    bundles = {}
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{split}"
    target = Path(out) / run_id
    target.mkdir(parents=True, exist_ok=True)

    with (target / "records.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for note_id in ids:
            gold = json.loads((corpus / "gold" / f"{note_id}.gold.json").read_text(encoding="utf-8"))
            context = gold["patient_context"]
            if context not in bundles:
                bundles[context] = json.loads((corpus / "patients" / f"{context}.json").read_text(encoding="utf-8"))
            mrn = next(e["resource"]["identifier"][0]["value"] for e in bundles[context]["entry"]
                       if e["resource"]["resourceType"] == "Patient")
            patient_id = patient_pseudo_id(mrn, BENCH_KEY)
            text = (corpus / "notes" / f"{note_id}.txt").read_bytes().decode("utf-8")
            record = run_note(client, note_id, gold["label"], context, text, gold["propositions"],
                              bundles[context], patient_id)
            record["split"] = split
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    (target / "env.json").write_text(json.dumps(environment(corpus), indent=2) + "\n", encoding="utf-8")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", default=str(ROOT / "benchmark" / "corpus" / "v1"))
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    parser.add_argument("--out", default=str(ROOT / "benchmark" / "results"))
    args = parser.parse_args(argv)
    target = run(args.corpus, args.split, args.out)
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
