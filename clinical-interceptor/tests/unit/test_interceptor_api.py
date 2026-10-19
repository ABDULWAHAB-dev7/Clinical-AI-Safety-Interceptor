import hashlib
import re
import unittest
import uuid

from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import JwtVerifier
from core.pseudo import patient_pseudo_id
from store.records import MemoryAuditStore
from tests.support import (
    ALL_SCOPES,
    FHIR_DIR,
    POLICY_PATH,
    PUBLIC_PEM,
    auth_headers,
    make_token,
)

KEY = b"interceptor-api-test-key-01234"
IDEM = {"Idempotency-Key": str(uuid.uuid4())}
NOTE = ("Chief complaint: Follow-up for type 2 diabetes. Patient denies known drug allergies. "
        "HbA1c 8.2% on 2026-09-02. Started amoxicillin 500 mg TID for sinusitis. Blood pressure stable.")
MRN = "SYNTH-MRN-0001"
PID = patient_pseudo_id(MRN, KEY)
ENC = "eid_" + "e" * 64

BUNDLE = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [
        {"resource": {"resourceType": "Patient", "id": "pat-0001",
                      "identifier": [{"system": "urn:synthetic:mrn", "value": MRN}]}},
        {"resource": {"resourceType": "AllergyIntolerance", "id": "allergy-penicillin-001",
                      "code": {"coding": [{"system": "http://snomed.info/sct", "code": "91936005",
                                           "display": "Allergy to penicillin"}]}}},
        {"resource": {"resourceType": "MedicationRequest", "id": "mr-1",
                      "medicationCodeableConcept": {"text": "metformin"}}},
        {"resource": {"resourceType": "Observation", "id": "obs-1", "status": "final",
                      "code": {"text": "Hgb"}, "valueQuantity": {"value": 12.1, "unit": "g/dL"}}},
    ],
}


def decompose_body(text=NOTE, **options):
    body = {"patient_pseudo_id": PID, "note": {"source_system": "EHR-SYNTH-01", "note_type": "PROGRESS_NOTE",
                                               "text": text}}
    if options:
        body["options"] = options
    return body


class Base(unittest.TestCase):
    def make(self, store=None, verifier=True):
        self.store = store or MemoryAuditStore()
        verifier = JwtVerifier(PUBLIC_PEM) if verifier else None
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=self.store, pseudo_key=KEY,
                         verifier=verifier)
        self.client = TestClient(app)


class AuthTests(Base):
    def setUp(self):
        self.make()

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], code)
        self.assertEqual(error["fail_mode"], "CLOSED")
        self.assertEqual(error["type"], "clinical_interception_error")
        return error

    def test_missing_token_is_401(self):
        self.assert_error(self.client.post("/v1/interceptor/decompose", json=decompose_body(), headers=IDEM),
                          401, "AUTH_TOKEN_INVALID")

    def test_auth_runs_before_body_validation(self):
        self.assert_error(self.client.post("/v1/interceptor/decompose", json={}, headers=IDEM),
                          401, "AUTH_TOKEN_INVALID")

    def test_no_verifier_configured_refuses_everything(self):
        self.make(verifier=False)
        headers = {"Authorization": f"Bearer {make_token()}"}
        self.assert_error(self.client.post("/v1/interceptor/decompose", json=decompose_body(), headers=IDEM | headers),
                          401, "AUTH_TOKEN_INVALID")

    def test_wrong_audience_is_401(self):
        headers = {"Authorization": f"Bearer {make_token(aud='someone-else')}"}
        self.assert_error(self.client.post("/v1/interceptor/decompose", json=decompose_body(), headers=IDEM | headers),
                          401, "AUTH_TOKEN_INVALID")

    def test_expired_token_is_401(self):
        headers = {"Authorization": f"Bearer {make_token(issued_at=1_000_000)}"}
        self.assert_error(self.client.post("/v1/interceptor/decompose", json=decompose_body(), headers=IDEM | headers),
                          401, "AUTH_TOKEN_INVALID")

    def test_lifetime_over_15_minutes_is_401(self):
        headers = {"Authorization": f"Bearer {make_token(lifetime=3600)}"}
        self.assert_error(self.client.post("/v1/interceptor/decompose", json=decompose_body(), headers=IDEM | headers),
                          401, "AUTH_TOKEN_INVALID")

    def test_token_signed_by_another_key_is_401(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        headers = {"Authorization": f"Bearer {make_token(key=other)}"}
        self.assert_error(self.client.post("/v1/interceptor/decompose", json=decompose_body(), headers=IDEM | headers),
                          401, "AUTH_TOKEN_INVALID")

    def test_missing_scope_is_403(self):
        headers = auth_headers("interceptor:verify")
        self.assert_error(self.client.post("/v1/interceptor/decompose", json=decompose_body(), headers=IDEM | headers),
                          403, "AUTH_SCOPE_INSUFFICIENT")


class EnvelopeAndHeaderTests(Base):
    def setUp(self):
        self.make()

    def test_contract_version_and_request_id_on_every_response(self):
        r = self.client.get("/v1/health")
        self.assertEqual(r.headers["X-Interceptor-Version"], "1.0.0")
        self.assertTrue(re.match(r"^[0-9a-f-]{36}$", r.headers["X-Request-Id"]))

    def test_request_id_is_echoed_when_valid(self):
        rid = str(uuid.uuid4())
        r = self.client.get("/v1/health", headers={"X-Request-Id": rid})
        self.assertEqual(r.headers["X-Request-Id"], rid)

    def test_invalid_request_id_is_replaced(self):
        r = self.client.get("/v1/health", headers={"X-Request-Id": "not-a-uuid"})
        self.assertNotEqual(r.headers["X-Request-Id"], "not-a-uuid")

    def test_non_json_post_is_415(self):
        r = self.client.post("/v1/interceptor/decompose", content="x", headers=IDEM | auth_headers())
        self.assertEqual(r.status_code, 415)
        self.assertEqual(r.json()["error"]["code"], "UNSUPPORTED_MEDIA_TYPE")

    def test_unknown_field_is_400_with_param(self):
        body = decompose_body() | {"policy_override": "pass"}
        r = self.client.post("/v1/interceptor/decompose", json=body, headers=IDEM | auth_headers())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "VALIDATION_FAILED")
        self.assertEqual(r.json()["error"]["param"], "policy_override")

    def test_oversized_body_is_413_before_reading(self):
        r = self.client.post("/v1/interceptor/decompose", json=decompose_body(),
                             headers=IDEM | auth_headers() | {"Content-Length": "3000000"})
        self.assertEqual(r.status_code, 413)

    def test_error_envelope_has_documented_fields(self):
        error = self.client.post("/v1/interceptor/decompose", json={}).json()["error"]
        for key in ("type", "code", "message", "param", "clinical_impact", "fail_mode", "retryable", "request_id",
                    "trace_id", "docs_url"):
            self.assertIn(key, error)


class DecomposeTests(Base):
    def setUp(self):
        self.make()

    def post(self, body=None, headers=None):
        return self.client.post("/v1/interceptor/decompose", json=body or decompose_body(),
                                headers=IDEM | auth_headers() | (headers or {}))

    def test_new_note_is_201_with_spec_claim_shape(self):
        r = self.post()
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertTrue(r.headers["Location"].startswith("/v1/interceptor/notes/"))
        self.assertEqual(body["char_length"], len(NOTE))
        self.assertEqual(body["claim_count"], len(body["claims"]))
        self.assertTrue(body["claims"])
        for claim in body["claims"]:
            self.assertRegex(claim["claim_id"], r"^clm_[0-9a-f]{12}$")
            self.assertEqual(NOTE[claim["char_start"]:claim["char_end"]], claim["claim_text"])
            self.assertEqual(NOTE[claim["source_sentence_start"]:claim["source_sentence_end"]], claim["source_sentence"])
            self.assertFalse(claim["claim_text"].endswith("."))

    def test_claim_id_follows_spec_derivation(self):
        body = self.post().json()
        sha = hashlib.sha256(NOTE.encode("utf-8")).hexdigest()
        first = body["claims"][0]
        expected = "clm_" + hashlib.sha256(
            f"{sha}:{first['char_start']}:{first['char_end']}:{first['claim_text']}".encode("utf-8")
        ).hexdigest()[:12]
        self.assertEqual(first["claim_id"], expected)

    def test_duplicate_note_is_200_with_same_note_id(self):
        first = self.post().json()
        second = self.client.post("/v1/interceptor/decompose", json=decompose_body(),
                                  headers={"Idempotency-Key": str(uuid.uuid4())} | auth_headers())
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["note_id"], first["note_id"])
        self.assertEqual(len(self.store.traces), 1)  # the duplicate writes no new trace

    def test_missing_idempotency_key_is_400(self):
        r = self.client.post("/v1/interceptor/decompose", json=decompose_body(), headers=auth_headers())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["param"], "Idempotency-Key")

    def test_note_over_200k_code_points_is_413(self):
        r = self.post(decompose_body("A. " * 70_000))  # 210,000 code points
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()["error"]["code"], "INTERCEPT_NOTE_TOO_LARGE")

    def test_blank_note_is_400(self):
        r = self.post(decompose_body("   "))
        self.assertEqual(r.status_code, 400)

    def test_claim_limit_is_422_and_nothing_is_stored(self):
        r = self.post(decompose_body(NOTE, max_claims=1))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "INTERCEPT_CLAIM_LIMIT_EXCEEDED")
        self.assertEqual(self.store.notes, {})

    def test_audit_failure_is_503_and_retryable(self):
        self.make(store=MemoryAuditStore(fail=True))
        r = self.post()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "AUDIT_WRITE_FAILED")
        self.assertTrue(r.json()["error"]["retryable"])
        self.assertEqual(r.headers["Retry-After"], "30")

    def test_bad_patient_pseudo_id_is_400(self):
        body = decompose_body() | {"patient_pseudo_id": "pid_short"}
        r = self.post(body)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["param"], "patient_pseudo_id")


class VerifyTests(Base):
    def setUp(self):
        self.make()
        created = self.client.post("/v1/interceptor/decompose", json=decompose_body(),
                                   headers=IDEM | auth_headers()).json()
        self.note_id = created["note_id"]

    def post(self, claims, **extra):
        body = {"patient_pseudo_id": PID, "fhir_bundle": BUNDLE, "claims": claims} | extra
        return self.client.post("/v1/interceptor/verify", json=body, headers=auth_headers())

    @staticmethod
    def claim(claim_id, text):
        return {"claim_id": claim_id, "claim_text": text, "char_start": 0, "char_end": len(text)}

    def test_contradicted_allergy_is_critical_and_entailed_medication_is_safe(self):
        r = self.post([
            self.claim("clm_000000000001", "No known drug allergies"),
            self.claim("clm_000000000002", "Patient is taking metformin"),
        ])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        by_id = {x["claim_id"]: x for x in body["results"]}
        allergy = by_id["clm_000000000001"]
        self.assertEqual(allergy["nli_class"], "CONTRADICTION")
        self.assertEqual(allergy["risk_severity"], "CRITICAL")
        self.assertEqual(allergy["risk_rule"], "contradiction_high_risk")
        self.assertEqual(allergy["matched_fhir_ref"], "AllergyIntolerance/allergy-penicillin-001")
        med = by_id["clm_000000000002"]
        self.assertEqual(med["nli_class"], "ENTAILMENT")
        self.assertEqual((med["risk_severity"], med["risk_rule"]), ("SAFE", "entailed"))
        self.assertEqual(med["matched_fhir_ref"], "MedicationRequest/mr-1")
        self.assertEqual(body["overall_risk_severity"], "CRITICAL")
        self.assertEqual(body["summary"]["by_severity"]["CRITICAL"], 1)

    def test_entailment_always_carries_a_matched_reference(self):
        r = self.post([self.claim("clm_000000000003", "Patient is taking metformin")])
        for result in r.json()["results"]:
            if result["nli_class"] == "ENTAILMENT":
                self.assertIsNotNone(result["matched_fhir_ref"])

    def test_claim_without_coded_match_is_503_fail_closed(self):
        r = self.post([self.claim("clm_000000000004", "Blood pressure stable")])
        self.assertEqual(r.status_code, 503)
        error = r.json()["error"]
        self.assertEqual(error["code"], "INTERCEPT_VERIFIER_UNAVAILABLE")
        self.assertTrue(error["retryable"])
        self.assertEqual(error["clinical_impact"], "BLOCKED")

    def test_patient_mismatch_is_422(self):
        body = {"patient_pseudo_id": "pid_" + "0" * 64, "fhir_bundle": BUNDLE,
                "claims": [self.claim("clm_000000000005", "Patient is taking metformin")]}
        r = self.client.post("/v1/interceptor/verify", json=body, headers=auth_headers())
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "INTERCEPT_PATIENT_MISMATCH")

    def test_non_collection_bundle_is_422(self):
        bad = BUNDLE | {"type": "searchset"}
        r = self.client.post("/v1/interceptor/verify",
                             json={"patient_pseudo_id": PID, "fhir_bundle": bad, "claims": []},
                             headers=auth_headers())
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "INTERCEPT_BUNDLE_INVALID")

    def test_duplicate_claim_ids_are_400(self):
        r = self.post([self.claim("clm_000000000006", "Patient is taking metformin"),
                       self.claim("clm_000000000006", "Patient is taking metformin")])
        self.assertEqual(r.status_code, 400)

    def test_unknown_note_is_404(self):
        r = self.post([self.claim("clm_000000000007", "Patient is taking metformin")],
                      note_id=str(uuid.uuid4()))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "NOTE_NOT_FOUND")

    def test_verify_with_note_writes_one_verify_trace(self):
        before = len(self.store.traces)
        r = self.post([self.claim("clm_000000000008", "Patient is taking metformin")], note_id=self.note_id)
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json()["verify_trace_id"])
        self.assertEqual(len(self.store.traces), before + 1)
        self.assertEqual(self.store.traces[-1]["stage"], "VERIFY")

    def test_verify_without_note_writes_nothing(self):
        before = len(self.store.traces)
        r = self.post([self.claim("clm_000000000009", "Patient is taking metformin")])
        self.assertIsNone(r.json()["verify_trace_id"])
        self.assertEqual(len(self.store.traces), before)

    def test_note_of_another_patient_is_422(self):
        other_mrn = "SYNTH-MRN-0002"
        other_pid = patient_pseudo_id(other_mrn, KEY)
        other_bundle = BUNDLE | {"entry": [
            {"resource": {"resourceType": "Patient", "id": "pat-0002",
                          "identifier": [{"system": "urn:synthetic:mrn", "value": other_mrn}]}}]}
        body = {"patient_pseudo_id": other_pid, "fhir_bundle": other_bundle, "note_id": self.note_id, "claims": []}
        r = self.client.post("/v1/interceptor/verify", json=body, headers=auth_headers())
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "AUDIT_NOTE_PATIENT_MISMATCH")


class HealthTests(Base):
    def setUp(self):
        self.make()

    def test_liveness_needs_no_token(self):
        self.assertEqual(self.client.get("/v1/health").json(), {"status": "ok"})

    def test_readiness_lists_components(self):
        r = self.client.get("/v1/health/ready")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["components"]["database"], "ok")
        self.assertEqual(r.json()["components"]["verifier"], "deterministic_only")


if __name__ == "__main__":
    unittest.main()
