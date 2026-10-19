import hashlib
import ssl
import unittest
import uuid

from fastapi.testclient import TestClient

from interceptor.api.app import create_app
from interceptor.api.auth import JwtVerifier
from interceptor.api.mtls import MtlsConfig, fingerprint_of_pem, identify_client, presented_fingerprint
from interceptor.api.ratelimit import RateLimiter
from interceptor.core.pseudo import patient_pseudo_id
from interceptor.store.records import MemoryAuditStore
from tests.support import (
    CLIENT_FINGERPRINT,
    FHIR_DIR,
    POLICY_PATH,
    PUBLIC_PEM,
    UNREGISTERED_FINGERPRINT,
    auth_headers,
    make_token,
    mtls_headers,
    security,
)

KEY = b"mtls-ratelimit-test-key-012345"
PID = patient_pseudo_id("SYNTH-MRN-0001", KEY)
BODY = {"patient_pseudo_id": PID, "note": {"source_system": "S", "note_type": "T", "text": "Patient reports headaches."}}
IDEM = {"Idempotency-Key": str(uuid.uuid4())}


def client(**kwargs):
    config = security() | kwargs
    app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=KEY,
                     verifier=JwtVerifier(PUBLIC_PEM), **config)
    return TestClient(app)


def post(c, headers):
    return c.post("/v1/interceptor/decompose", json=BODY, headers=IDEM | headers)


class MtlsTests(unittest.TestCase):
    def assert_mtls_refused(self, response):
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "AUTH_MTLS_REQUIRED")

    def test_registered_certificate_and_token_are_accepted(self):
        self.assertEqual(post(client(), auth_headers()).status_code, 201)

    def test_token_alone_is_refused(self):
        self.assert_mtls_refused(post(client(), {"Authorization": f"Bearer {make_token()}"}))

    def test_certificate_alone_is_refused_as_a_missing_token(self):
        r = post(client(), mtls_headers())
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (401, "AUTH_TOKEN_INVALID"))

    def test_unregistered_certificate_is_refused_even_with_a_valid_token(self):
        headers = {"Authorization": f"Bearer {make_token()}"} | mtls_headers(UNREGISTERED_FINGERPRINT)
        self.assert_mtls_refused(post(client(), headers))

    def test_wrong_proxy_secret_is_refused(self):
        headers = {"Authorization": f"Bearer {make_token()}"} | mtls_headers(secret="not-the-secret")
        self.assert_mtls_refused(post(client(), headers))

    def test_fingerprint_header_from_an_untrusted_address_is_ignored(self):
        # a client that is not the trusted proxy cannot claim a certificate by sending the header
        c = client(**security(trusted=("10.9.9.9",)))
        self.assert_mtls_refused(post(c, auth_headers()))

    def test_default_registry_refuses_every_clinical_request(self):
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=KEY,
                         verifier=JwtVerifier(PUBLIC_PEM))
        self.assert_mtls_refused(post(TestClient(app), auth_headers()))

    def test_certificate_is_checked_before_the_token(self):
        self.assert_mtls_refused(post(client(), {}))

    def test_health_needs_no_certificate(self):
        self.assertEqual(client().get("/v1/health").status_code, 200)

    def test_direct_tls_extension_supplies_the_fingerprint(self):
        pem = ssl.DER_cert_to_PEM_cert(b"0\x82\x01\x00" + b"x" * 252)  # any DER blob; only its SHA-256 matters here
        expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
        self.assertEqual(fingerprint_of_pem(pem), expected)

        class Req:
            scope = {"extensions": {"tls": {"client_cert_chain": [pem]}}}
            client = None
            headers = {}

        config = MtlsConfig({expected: "direct-client"})
        self.assertEqual(presented_fingerprint(Req, config), expected)
        self.assertEqual(identify_client(Req, config), "direct-client")

    def test_registry_file_loading(self):
        import json
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            path.write_text(json.dumps({"clients": [{"client_id": "ehr-gateway",
                                                      "fingerprint_sha256": CLIENT_FINGERPRINT.upper(),
                                                      "patient_scope": "all"}]}))
            config = MtlsConfig.from_registry_file(path, trusted_proxies=["10.0.0.1"], proxy_secret=b"s")
        self.assertEqual(config.registry, {CLIENT_FINGERPRINT: "ehr-gateway"})
        self.assertEqual(config.trusted_proxies, frozenset({"10.0.0.1"}))

    def test_malformed_registry_entry_is_rejected(self):
        import json
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            path.write_text(json.dumps({"clients": [{"client_id": "x", "fingerprint_sha256": "short"}]}))
            with self.assertRaises(ValueError):
                MtlsConfig.from_registry_file(path)


class RateLimitTests(unittest.TestCase):
    def test_limit_is_enforced_with_retry_after_and_headers(self):
        c = client(**security(rate_limit=2))
        first = post(c, auth_headers())
        self.assertEqual(first.headers["RateLimit-Limit"], "2")
        self.assertEqual(first.headers["RateLimit-Remaining"], "1")
        self.assertEqual(post(c, auth_headers()).headers["RateLimit-Remaining"], "0")
        third = post(c, auth_headers())
        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.json()["error"]["code"], "RATE_LIMITED")
        self.assertTrue(third.json()["error"]["retryable"])
        self.assertGreaterEqual(int(third.headers["Retry-After"]), 1)
        self.assertEqual(third.headers["RateLimit-Remaining"], "0")

    def test_window_resets(self):
        now = [0.0]
        limiter = RateLimiter(1, clock=lambda: now[0])
        self.assertTrue(limiter.hit("a").allowed)
        self.assertFalse(limiter.hit("a").allowed)
        now[0] = 61.0
        self.assertTrue(limiter.hit("a").allowed)

    def test_clients_are_counted_separately(self):
        limiter = RateLimiter(1)
        self.assertTrue(limiter.hit("a").allowed)
        self.assertTrue(limiter.hit("b").allowed)
        self.assertFalse(limiter.hit("a").allowed)

    def test_requests_refused_before_the_limiter_do_not_use_the_quota(self):
        limiter = RateLimiter(1)
        c = client(mtls=security()["mtls"], limiter=limiter)
        post(c, {"Authorization": f"Bearer {make_token()}"})  # refused: no certificate
        self.assertEqual(post(c, auth_headers()).status_code, 201)

    def test_invalid_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            RateLimiter(0)


if __name__ == "__main__":
    unittest.main()
