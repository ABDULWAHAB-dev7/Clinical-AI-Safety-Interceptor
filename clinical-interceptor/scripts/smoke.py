"""End-to-end smoke test against a RUNNING server (PLAN 5.6): decompose, verify, audit, then read the report back.

    python scripts/smoke.py --base https://127.0.0.1:8443 --certs seed/certs

It also checks the security edges over real TLS: no client certificate (the handshake must fail), a certificate that is
signed by the CA but not registered (403), and a registered certificate with no token (401).
Start the server with scripts/dev_server.py and the same INTERCEPTOR_PSEUDO_KEY passed here.
"""

import argparse
import ssl
import sys
import time
import uuid
from pathlib import Path

import httpx
import jwt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interceptor.core.pseudo import patient_pseudo_id  # noqa: E402

NOTE = ("Chief complaint: Follow-up for type 2 diabetes. Patient denies known drug allergies. HbA1c 8.2% on "
        "2026-09-02. Started amoxicillin 500 mg TID for sinusitis. Blood pressure stable.")
MRN = "SYNTH-MRN-0001"
BUNDLE = {
    "resourceType": "Bundle", "type": "collection",
    "entry": [
        {"resource": {"resourceType": "Patient", "id": "pat-0001",
                      "identifier": [{"system": "urn:synthetic:mrn", "value": MRN}]}},
        {"resource": {"resourceType": "AllergyIntolerance", "id": "allergy-penicillin-001",
                      "verificationStatus": {"coding": [{"code": "confirmed"}]},
                      "code": {"coding": [{"system": "http://snomed.info/sct", "code": "91936005",
                                           "display": "Allergy to penicillin"}]},
                      "patient": {"reference": "Patient/pat-0001"}}},
        {"resource": {"resourceType": "Condition", "id": "cond-t2dm-001",
                      "clinicalStatus": {"coding": [{"code": "active"}]},
                      "code": {"coding": [{"system": "http://snomed.info/sct", "code": "44054006",
                                           "display": "Diabetes mellitus type 2"}]},
                      "subject": {"reference": "Patient/pat-0001"}}},
    ],
}
SCOPES = "interceptor:decompose interceptor:verify interceptor:audit:write interceptor:read"


def tls_context(ca, cert=None):
    """An explicit SSL context. (httpx's verify=<file> with cert=... did not present the client certificate here.)"""
    context = ssl.create_default_context(cafile=ca)
    if cert:
        context.load_cert_chain(*cert)
    return context


def check(condition, message):
    print(("PASS " if condition else "FAIL ") + message)
    if not condition:
        raise SystemExit(1)


def token(private_key):
    now = int(time.time())
    return jwt.encode({"sub": "smoke", "aud": "clinical-interceptor", "iat": now, "exp": now + 600, "scope": SCOPES},
                      private_key, algorithm="ES256")


def result_for(claim):
    text = claim["claim_text"]
    base = {"result_id": str(uuid.uuid4()), "claim_id": claim["claim_id"], "matched_fhir_ref": None,
            "nli_class": "NEUTRAL", "confidence": 0.9, "risk_severity": "WARNING", "risk_rule": "unsupported_clinical",
            "verifier_version": "smoke"}
    if "denies known drug allergies" in text:
        base |= {"nli_class": "CONTRADICTION", "risk_severity": "CRITICAL", "risk_rule": "contradiction_high_risk",
                 "matched_fhir_ref": "AllergyIntolerance/allergy-penicillin-001"}
    return base


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://127.0.0.1:8443")
    parser.add_argument("--certs", default=str(ROOT / "seed" / "certs"))
    parser.add_argument("--pseudo-key", required=True, help="the server's INTERCEPTOR_PSEUDO_KEY")
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds per request; remote databases are slower")
    args = parser.parse_args()
    certs = Path(args.certs)
    ca = str(certs / "ca.pem")
    registered = (str(certs / "client-registered.pem"), str(certs / "client-registered.key"))
    unregistered = (str(certs / "client-unregistered.pem"), str(certs / "client-unregistered.key"))
    bearer = {"Authorization": f"Bearer {token((certs / 'jwt_private.pem').read_text())}"}
    pid = patient_pseudo_id(MRN, args.pseudo_key.encode("utf-8"))

    # --- the security edges, over real TLS ---
    try:
        httpx.get(f"{args.base}/v1/health", verify=tls_context(ca))
        check(False, "a connection with no client certificate is refused")
    except httpx.TransportError:
        check(True, "a connection with no client certificate is refused at the TLS handshake")

    with httpx.Client(base_url=args.base, verify=tls_context(ca, unregistered), timeout=args.timeout) as client:
        r = client.post("/v1/interceptor/verify", json={}, headers=bearer)
        check(r.status_code == 403 and r.json()["error"]["code"] == "AUTH_MTLS_REQUIRED",
              "a CA-signed but unregistered certificate gets 403 AUTH_MTLS_REQUIRED")

    with httpx.Client(base_url=args.base, verify=tls_context(ca, registered), timeout=args.timeout) as client:
        check(client.get("/v1/health").json() == {"status": "ok"}, "health answers with a registered certificate")
        r = client.post("/v1/interceptor/verify", json={})
        check(r.status_code == 401, "a registered certificate with no bearer token gets 401")

        # --- decompose -> verify -> audit -> read the report ---
        key = {"Idempotency-Key": str(uuid.uuid4())}
        decompose = {"patient_pseudo_id": pid, "note": {"source_system": "EHR-SYNTH-01", "note_type": "PROGRESS_NOTE",
                                                        "text": NOTE}}
        r = client.post("/v1/interceptor/decompose", json=decompose, headers=bearer | key)
        check(r.status_code in (200, 201), f"decompose returns {r.status_code}")
        note = r.json()
        if r.status_code == 201:
            check(r.headers["Location"].endswith(note["note_id"]), "decompose 201 has a Location header")
        check(len(note["claims"]) >= 4, f"decompose found {len(note['claims'])} claims")

        claims = [c for c in note["claims"] if "type 2 diabetes" in c["claim_text"]
                  or "denies known drug allergies" in c["claim_text"]]
        verify = {"patient_pseudo_id": pid, "note_id": note["note_id"], "fhir_bundle": BUNDLE,
                  "claims": [{"claim_id": c["claim_id"], "claim_text": c["claim_text"], "char_start": c["char_start"],
                              "char_end": c["char_end"], "entities": c["entities"]} for c in claims]}
        r = client.post("/v1/interceptor/verify", json=verify, headers=bearer)
        check(r.status_code == 200, f"verify returns {r.status_code}")
        verified = r.json()
        check(verified["overall_risk_severity"] == "CRITICAL", "verify finds the allergy denial CRITICAL")

        audit = {"note_id": note["note_id"], "patient_pseudo_id": pid, "verify_trace_ids": [verified["verify_trace_id"]],
                 "claims": note["claims"], "results": [result_for(c) for c in note["claims"]]}
        r = client.post("/v1/interceptor/audit", json=audit, headers=bearer | {"Idempotency-Key": str(uuid.uuid4())})
        check(r.status_code == 201, f"audit returns {r.status_code}")
        check(bool(r.headers.get("Location")), "audit 201 has a Location header")
        report = r.json()
        check(report["pipeline_status"] == "BLOCKED", "the report blocks the note on a CRITICAL event")

        r = client.get(f"/v1/interceptor/audit/reports/{report['report_id']}", headers=bearer)
        check(r.status_code == 200 and r.json()["report_id"] == report["report_id"], "the report reads back")
        r = client.get(f"/v1/interceptor/notes/{note['note_id']}", headers=bearer)
        check(r.status_code == 200 and r.json()["pipeline_status"] == "BLOCKED", "the note reads back as BLOCKED")
    print("smoke test passed")


if __name__ == "__main__":
    main()
