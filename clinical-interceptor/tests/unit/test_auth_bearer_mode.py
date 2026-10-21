"""Bearer-only demo mode (INTERCEPTOR_AUTH_MODE=bearer, docs/api-changes/2026-10-21-bearer-demo.md).

The default mode still needs a client certificate and a token. The demo mode needs only the token, and it is refused at
startup unless INTERCEPTOR_DEPLOYMENT_PROFILE=vercel-demo.
"""

import os
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

from interceptor.api.app import build_default_app, create_app
from interceptor.api.auth import JwtVerifier
from interceptor.store.records import MemoryAuditStore
from tests.support import FHIR_DIR, POLICY_PATH, PRIVATE_PEM, PUBLIC_PEM, auth_headers, make_token, security

KEY = b"bearer-mode-test-key-0123456789"
READ_SCOPE = "interceptor:read"


def bearer_app():
    return create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=KEY,
                      verifier=JwtVerifier(PUBLIC_PEM), auth_mode="bearer")


def note_url():
    return f"/v1/interceptor/notes/{uuid.uuid4()}"


class BearerModeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(bearer_app())

    def test_valid_token_is_accepted_without_a_certificate(self):
        # A missing note answers 404, which means the request got past authentication and authorization.
        response = self.client.get(note_url(), headers={"Authorization": f"Bearer {make_token(READ_SCOPE)}"})
        self.assertEqual(response.status_code, 404)

    def test_missing_token_is_refused(self):
        response = self.client.get(note_url())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_TOKEN_INVALID")

    def test_wrong_audience_is_refused(self):
        token = make_token(READ_SCOPE, aud="someone-else")
        response = self.client.get(note_url(), headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)

    def test_expired_token_is_refused(self):
        token = make_token(READ_SCOPE, lifetime=-60)
        response = self.client.get(note_url(), headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)

    def test_missing_scope_is_refused(self):
        token = make_token("interceptor:intercept")
        response = self.client.get(note_url(), headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "AUTH_SCOPE_INSUFFICIENT")

    def test_token_without_subject_is_refused(self):
        import time

        import jwt

        now = int(time.time())
        token = jwt.encode({"aud": "clinical-interceptor", "iat": now, "exp": now + 600, "scope": READ_SCOPE},
                           PRIVATE_PEM, algorithm="ES256")
        response = self.client.get(note_url(), headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401)


class DefaultModeIsUnchangedTests(unittest.TestCase):
    def test_default_mode_still_refuses_a_request_without_a_certificate(self):
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=KEY,
                         verifier=JwtVerifier(PUBLIC_PEM), **security())
        response = TestClient(app).get(note_url(), headers={"Authorization": f"Bearer {make_token(READ_SCOPE)}"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "AUTH_MTLS_REQUIRED")

    def test_default_mode_accepts_both_factors(self):
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=KEY,
                         verifier=JwtVerifier(PUBLIC_PEM), **security())
        response = TestClient(app).get(note_url(), headers=auth_headers(READ_SCOPE))
        self.assertEqual(response.status_code, 404)


class StartupGuardTests(unittest.TestCase):
    def test_bearer_mode_needs_a_token_verifier(self):
        with self.assertRaises(ValueError):
            create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=KEY,
                       auth_mode="bearer")

    def test_unknown_auth_mode_is_refused(self):
        with self.assertRaises(ValueError):
            create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=MemoryAuditStore(), pseudo_key=KEY,
                       verifier=JwtVerifier(PUBLIC_PEM), auth_mode="none")

    def test_bearer_mode_outside_the_demo_profile_refuses_to_start(self):
        env = {"INTERCEPTOR_DATABASE_URL": "host=127.0.0.1 port=1 dbname=x", "INTERCEPTOR_PSEUDO_KEY": "k" * 32,
               "INTERCEPTOR_AUTH_MODE": "bearer", "INTERCEPTOR_JWT_PUBLIC_KEY_FILE": "unused"}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.dict(os.environ, {"INTERCEPTOR_DEPLOYMENT_PROFILE": ""}):
            with self.assertRaises(RuntimeError):
                build_default_app()


if __name__ == "__main__":
    unittest.main()
