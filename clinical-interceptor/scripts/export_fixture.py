"""Exports a UI fixture from the real pipeline output (PLAN 6.2): decompose, verify and audit run in process through
the same endpoints a client would call, against the seed case.

    python scripts/export_fixture.py --case case-0001 --out web/src/lib/fixtures/case-0001.json

The fixture holds the note text (synthetic seed data only) and the audit report. patient_pseudo_id is cut to the first
8 hex characters, for display only. Claims that need the NLI model are scored by a neutral stand-in, because no
checkpoint is chosen yet (Gate G1); the fixture's `meta.nli` says so.
"""

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from interceptor.api.app import create_app  # noqa: E402
from interceptor.api.auth import JwtVerifier  # noqa: E402
from interceptor.api.mtls import MtlsConfig  # noqa: E402
from interceptor.api.ratelimit import RateLimiter  # noqa: E402
from interceptor.core.pseudo import patient_pseudo_id  # noqa: E402
from interceptor.store.records import MemoryAuditStore  # noqa: E402

SEED = ROOT / "seed"
PSEUDO_KEY = b"export-fixture-key-0123456789abcdef"
SCOPES = "interceptor:decompose interceptor:verify interceptor:audit:write interceptor:read"
FINGERPRINT = hashlib.sha256(b"export-fixture-client").hexdigest()
PROXY_SECRET = "export-fixture-proxy-secret-0123456789"


class NeutralNli:
    """Stand-in for the unchosen NLI model: every claim that needs it comes back NEUTRAL."""

    def score(self, premise, hypothesis):
        return {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1}


def seed_bundle():
    entries = []
    for path in sorted((SEED / "fhir").glob("*.ndjson")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append({"resource": json.loads(line)})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


def mrn_of(bundle):
    for entry in bundle["entry"]:
        if entry["resource"]["resourceType"] == "Patient":
            return entry["resource"]["identifier"][0]["value"]
    raise SystemExit("seed has no Patient")


def build_client():
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    app = create_app(
        policy_path=ROOT / "config" / "policy" / "default.yaml", fhir_dir=SEED / "fhir", store=MemoryAuditStore(),
        pseudo_key=PSEUDO_KEY, verifier=JwtVerifier(public), nli_model=NeutralNli(),
        mtls=MtlsConfig({FINGERPRINT: "export-fixture"}, frozenset({"testclient"}), PROXY_SECRET.encode("ascii"),
                        {"export-fixture": None}),
        limiter=RateLimiter(100000),
    )
    now = int(time.time())
    token = jwt.encode({"sub": "export-fixture", "aud": "clinical-interceptor", "iat": now, "exp": now + 600,
                        "scope": SCOPES}, private, algorithm="ES256")
    headers = {"Authorization": f"Bearer {token}", "X-Client-Cert-SHA256": FINGERPRINT,
               "X-Proxy-Secret": PROXY_SECRET}
    return TestClient(app), headers


def export(case):
    note_path = SEED / "notes" / f"{case}.txt"
    if not note_path.exists():
        raise SystemExit(f"unknown case {case!r}: no {note_path}")
    text = note_path.read_text(encoding="utf-8").strip("\n")
    bundle = seed_bundle()
    pid = patient_pseudo_id(mrn_of(bundle), PSEUDO_KEY)
    client, headers = build_client()

    def call(method, path, body=None, idempotent=False):
        extra = {"Idempotency-Key": str(uuid.uuid4())} if idempotent else {}
        response = client.request(method, path, json=body, headers=headers | extra)
        if response.status_code >= 300:
            raise SystemExit(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()

    note = call("POST", "/v1/interceptor/decompose", {
        "patient_pseudo_id": pid,
        "note": {"source_system": "EHR-SYNTH-01", "note_type": "PROGRESS_NOTE", "text": text}}, idempotent=True)
    claims = note["claims"]
    verified = call("POST", "/v1/interceptor/verify", {
        "patient_pseudo_id": pid, "note_id": note["note_id"], "fhir_bundle": bundle,
        "claims": [{k: c[k] for k in ("claim_id", "claim_text", "char_start", "char_end", "entities")}
                   for c in claims]})
    report = call("POST", "/v1/interceptor/audit", {
        "note_id": note["note_id"], "patient_pseudo_id": pid, "verify_trace_ids": [verified["verify_trace_id"]],
        "claims": claims, "results": verified["results"]}, idempotent=True)

    report["patient_pseudo_id"] = pid.removeprefix("pid_")[:8]
    return {
        "case_id": case,
        "meta": {"generated_by": "scripts/export_fixture.py", "note_synthetic": True, "nli": "neutral-stand-in",
                 "verifier_version": verified["verifier_version"]},
        "note_text": text,
        "report": report,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="case-0001")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    fixture = export(args.case)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    spans = fixture["report"]["attribution_spans"]
    print(f"wrote {out} ({len(spans)} spans, {fixture['report']['counts']})")


if __name__ == "__main__":
    main()
