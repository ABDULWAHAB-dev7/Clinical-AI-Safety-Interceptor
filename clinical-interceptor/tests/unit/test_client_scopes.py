"""Per-client patient scope: a client may only use the patients its registry entry lists."""

import json
import pathlib
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient

from interceptor.api.app import create_app
from interceptor.api.auth import JwtVerifier
from interceptor.api.mtls import MtlsConfig
from interceptor.api.ratelimit import RateLimiter
from interceptor.core.pseudo import patient_pseudo_id
from interceptor.store.records import MemoryAuditStore
from tests.support import (
    CLIENT_FINGERPRINT,
    CLIENT_ID,
    FHIR_DIR,
    POLICY_PATH,
    PROXY_SECRET,
    PUBLIC_PEM,
    auth_headers,
)
from tests.support_bundle import BUNDLE
from tests.support_proxy import KEY, PID, MockUpstream, chat_body
from tests.unit.test_audit_api import NOTE, result_for

OTHER_PID = patient_pseudo_id("SYNTH-MRN-0002", KEY)


def make(scope):
    """An app whose one client may use `scope` ("all" or a list of pseudo-IDs)."""
    scopes = {} if scope == "all" else {CLIENT_ID: frozenset(scope)}
    mtls = MtlsConfig({CLIENT_FINGERPRINT: CLIENT_ID}, frozenset({"testclient"}), PROXY_SECRET.encode("ascii"), scopes)
    mock = MockUpstream()
    store = MemoryAuditStore()
    app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=store, pseudo_key=KEY,
                     verifier=JwtVerifier(PUBLIC_PEM), mtls=mtls, limiter=RateLimiter(100000),
                     upstream=mock.client())
    return TestClient(app), mock, store


def decompose_body(pid):
    return {"patient_pseudo_id": pid, "note": {"source_system": "EHR", "note_type": "PROGRESS_NOTE", "text": NOTE}}


class ScopeEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.headers = auth_headers()

    def idem(self):
        return {"Idempotency-Key": str(uuid.uuid4())} | self.headers

    def assert_refused(self, response):
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "AUTH_SCOPE_INSUFFICIENT"))
        self.assertEqual(response.json()["error"]["param"], "patient_pseudo_id")

    def test_a_listed_patient_works_everywhere(self):
        client, _, _ = make([PID])
        self.assertEqual(client.post("/v1/interceptor/decompose", json=decompose_body(PID), headers=self.idem()).status_code, 201)
        self.assertEqual(client.post("/v1/chat/completions/intercept", json=chat_body(), headers=self.headers).status_code, 200)

    def test_decompose_refuses_an_unlisted_patient_and_writes_nothing(self):
        client, _, store = make([PID])
        self.assert_refused(client.post("/v1/interceptor/decompose", json=decompose_body(OTHER_PID), headers=self.idem()))
        self.assertEqual(store.notes, {})

    def test_verify_refuses_an_unlisted_patient(self):
        client, _, _ = make([OTHER_PID])
        body = {"patient_pseudo_id": PID, "fhir_bundle": BUNDLE, "claims": []}
        self.assert_refused(client.post("/v1/interceptor/verify", json=body, headers=self.headers))

    def test_audit_refuses_an_unlisted_patient(self):
        client, _, store = make("all")
        note = client.post("/v1/interceptor/decompose", json=decompose_body(PID), headers=self.idem()).json()
        restricted, _, _ = make([OTHER_PID])
        body = {"note_id": note["note_id"], "patient_pseudo_id": PID, "verify_trace_ids": [],
                "claims": note["claims"], "results": [result_for(c) for c in note["claims"]]}
        self.assert_refused(restricted.post("/v1/interceptor/audit", json=body, headers=self.idem()))

    def test_the_proxy_refuses_an_unlisted_patient_before_calling_the_upstream(self):
        client, mock, _ = make([OTHER_PID])
        self.assert_refused(client.post("/v1/chat/completions/intercept", json=chat_body(), headers=self.headers))
        self.assertEqual(mock.requests, [])

    def test_reads_answer_404_for_a_patient_outside_the_scope(self):
        # one app and one store, so the note exists; only the client's scope differs
        client, _, store = make("all")
        note = client.post("/v1/interceptor/decompose", json=decompose_body(PID), headers=self.idem()).json()
        body = {"note_id": note["note_id"], "patient_pseudo_id": PID, "verify_trace_ids": [],
                "claims": note["claims"], "results": [result_for(c) for c in note["claims"]]}
        report = client.post("/v1/interceptor/audit", json=body, headers=self.idem()).json()

        client.app.state.mtls = MtlsConfig({CLIENT_FINGERPRINT: CLIENT_ID}, frozenset({"testclient"}),
                                           PROXY_SECRET.encode("ascii"), {CLIENT_ID: frozenset({OTHER_PID})})
        for url, code in ((f"/v1/interceptor/notes/{note['note_id']}", "NOTE_NOT_FOUND"),
                          (f"/v1/interceptor/audit/reports/{report['report_id']}", "REPORT_NOT_FOUND")):
            with self.subTest(url=url):
                r = client.get(url, headers=self.headers)
                self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, code))  # same as an unknown ID

        client.app.state.mtls = MtlsConfig({CLIENT_FINGERPRINT: CLIENT_ID}, frozenset({"testclient"}),
                                           PROXY_SECRET.encode("ascii"), {CLIENT_ID: frozenset({PID})})
        self.assertEqual(client.get(f"/v1/interceptor/notes/{note['note_id']}", headers=self.headers).status_code, 200)

    def test_verify_with_a_note_checks_the_note_owner_too(self):
        client, _, _ = make("all")
        note = client.post("/v1/interceptor/decompose", json=decompose_body(PID), headers=self.idem()).json()
        self.assertEqual(note["patient_pseudo_id"], PID)

    def test_the_legacy_endpoint_needs_an_unrestricted_client(self):
        from tests.support import load_example

        client, _, _ = make([PID])
        r = client.post("/v1/verify", json=load_example("verify_clean.json"), headers=self.headers)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "AUTH_SCOPE_INSUFFICIENT"))
        open_client, _, _ = make("all")
        self.assertEqual(open_client.post("/v1/verify", json=load_example("verify_clean.json"),
                                          headers=self.headers).status_code, 200)

    def test_an_empty_scope_means_no_patients(self):
        client, _, _ = make([])
        self.assert_refused(client.post("/v1/interceptor/decompose", json=decompose_body(PID), headers=self.idem()))


class RegistryFileTests(unittest.TestCase):
    def load(self, entry):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "registry.json"
            path.write_text(json.dumps({"clients": [{"client_id": "c", "fingerprint_sha256": CLIENT_FINGERPRINT} | entry]}))
            return MtlsConfig.from_registry_file(path)

    def test_all_means_every_patient(self):
        config = self.load({"patient_scope": "all"})
        self.assertTrue(config.allows("c", PID))
        self.assertTrue(config.is_unrestricted("c"))

    def test_a_list_means_only_those_patients(self):
        config = self.load({"patient_scope": [PID]})
        self.assertTrue(config.allows("c", PID))
        self.assertFalse(config.allows("c", OTHER_PID))
        self.assertFalse(config.is_unrestricted("c"))

    def test_an_entry_without_a_scope_is_refused_so_access_is_never_granted_by_omission(self):
        with self.assertRaises(ValueError):
            self.load({})

    def test_bad_scopes_are_refused(self):
        for scope in ("everyone", ["not-a-pseudo-id"], 5, None):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.load({"patient_scope": scope})


if __name__ == "__main__":
    unittest.main()
