"""Contract tests: real responses against the strict shapes in api/schemas.py, and API.md's own examples.

Where an API.md example cannot work as written, a test here pins what the service does instead, so the conflict stays
visible (see the README, "Conflicts and choices in API.md").
"""

import unittest
import uuid

from fastapi.testclient import TestClient

from interceptor.api.app import create_app
from interceptor.api.auth import JwtVerifier
from interceptor.api.schemas import (
    AuditReport,
    ChatCompletion,
    DecomposeResponse,
    ErrorEnvelope,
    NoteView,
    VerifyResponse,
)
from interceptor.store.records import MemoryAuditStore
from tests.support import FHIR_DIR, POLICY_PATH, PUBLIC_PEM, auth_headers, security
from tests.support_bundle import BUNDLE
from tests.support_proxy import KEY, PID, MockUpstream, chat_body
from tests.unit.test_audit_api import NOTE, result_for

# API.md 5.1.2: the decompose example request
DECOMPOSE_EXAMPLE = {
    "patient_pseudo_id": "pid_9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e9c1e",
    "encounter_pseudo_id": None,
    "note": {"source_system": "EHR-SYNTH-01", "note_type": "PROGRESS_NOTE", "text": NOTE},
    "options": {"max_claims": 200, "include_entities": True},
}
# API.md 4.1.2 and 6.1.2: a bundle whose Patient has no identifier
EXAMPLE_BUNDLE_WITHOUT_IDENTIFIER = {
    "resourceType": "Bundle", "type": "collection",
    "entry": [{"resource": {"resourceType": "Patient", "id": "pat-0001", "gender": "female",
                            "birthDate": "1968-03-14", "active": True}}],
}


class ContractBase(unittest.TestCase):
    def setUp(self):
        self.store = MemoryAuditStore()
        self.mock = MockUpstream()
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=self.store, pseudo_key=KEY,
                         verifier=JwtVerifier(PUBLIC_PEM), upstream=self.mock.client(), **security())
        self.client = TestClient(app)
        self.headers = auth_headers()

    def idem(self):
        return {"Idempotency-Key": str(uuid.uuid4())} | self.headers

    def decompose(self):
        body = DECOMPOSE_EXAMPLE | {"patient_pseudo_id": PID}
        return self.client.post("/v1/interceptor/decompose", json=body, headers=self.idem())


class ResponseShapeTests(ContractBase):
    def test_decompose_response(self):
        r = self.decompose()
        self.assertEqual(r.status_code, 201)
        DecomposeResponse.model_validate(r.json())

    def test_decompose_replay_has_the_same_shape(self):
        self.decompose()
        DecomposeResponse.model_validate(self.decompose().json())

    def test_verify_response(self):
        body = {"patient_pseudo_id": PID, "fhir_bundle": BUNDLE, "claims": [
            {"claim_id": "clm_000000000001", "claim_text": "No known drug allergies", "char_start": 0, "char_end": 23},
            {"claim_id": "clm_000000000002", "claim_text": "Patient has type 2 diabetes", "char_start": 0,
             "char_end": 27}]}
        r = self.client.post("/v1/interceptor/verify", json=body, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        VerifyResponse.model_validate(r.json())

    def test_audit_report_and_both_reads(self):
        note = self.decompose().json()
        body = {"note_id": note["note_id"], "patient_pseudo_id": PID, "verify_trace_ids": [],
                "claims": note["claims"], "results": [result_for(c) for c in note["claims"]]}
        r = self.client.post("/v1/interceptor/audit", json=body, headers=self.idem())
        self.assertEqual(r.status_code, 201)
        report = AuditReport.model_validate(r.json())
        read = self.client.get(f"/v1/interceptor/audit/reports/{report.report_id}", headers=self.headers)
        AuditReport.model_validate(read.json())
        NoteView.model_validate(self.client.get(f"/v1/interceptor/notes/{note['note_id']}",
                                                headers=self.headers).json())

    def test_proxy_response_redacted_blocked_and_released(self):
        for text, policy in ((None, None), (None, {"critical_action": "BLOCK_RESPONSE"}),
                             ("HbA1c 8.2% on 2026-09-02.", None)):
            with self.subTest(text=text, policy=policy):
                if text:
                    self.mock.text = text
                body = chat_body(interception=policy) if policy else chat_body()
                r = self.client.post("/v1/chat/completions/intercept", json=body, headers=self.headers)
                self.assertEqual(r.status_code, 200)
                ChatCompletion.model_validate(r.json())

    def test_proxy_response_with_claims_included(self):
        r = self.client.post("/v1/chat/completions/intercept",
                             json=chat_body(interception={"include_claims": True}), headers=self.headers)
        ChatCompletion.model_validate(r.json())

    def test_every_error_has_the_error_envelope(self):
        cases = [
            self.client.post("/v1/interceptor/decompose", json={}, headers=self.idem()),  # 400 validation
            self.client.post("/v1/interceptor/decompose", json={}),  # 403 no certificate
            self.client.get(f"/v1/interceptor/notes/{uuid.uuid4()}", headers=self.headers),  # 404
            self.client.post("/v1/interceptor/verify", json={"patient_pseudo_id": PID,
                                                              "fhir_bundle": {"resourceType": "Bundle"}},
                             headers=self.headers),  # 422
            self.client.post("/v1/chat/completions/intercept", json=chat_body(model="unknown"),
                             headers=self.headers),  # 400
        ]
        for response in cases:
            with self.subTest(status=response.status_code):
                self.assertGreaterEqual(response.status_code, 400)
                ErrorEnvelope.model_validate(response.json())

    def test_the_error_request_id_matches_the_response_header(self):
        r = self.client.get(f"/v1/interceptor/notes/{uuid.uuid4()}", headers=self.headers)
        self.assertEqual(r.json()["error"]["request_id"], r.headers["X-Request-Id"])

    def test_every_response_carries_the_contract_version(self):
        for r in (self.client.get("/v1/health"), self.decompose(),
                  self.client.get(f"/v1/interceptor/notes/{uuid.uuid4()}", headers=self.headers)):
            self.assertEqual(r.headers["X-Interceptor-Version"], "1.0.0")


class ApiMdExampleTests(ContractBase):
    """API.md's own examples. Some cannot work as written; these tests say exactly how the service differs."""

    def test_the_decompose_example_works_as_written(self):
        body = DECOMPOSE_EXAMPLE  # the placeholder pid_ value is valid; decompose does not check it against a bundle
        r = self.client.post("/v1/interceptor/decompose", json=body, headers=self.idem())
        self.assertEqual(r.status_code, 201)
        DecomposeResponse.model_validate(r.json())

    def test_the_example_bundle_has_no_patient_identifier_so_it_is_refused(self):
        # API.md says the pseudo-ID must match the bundle's Patient, but the example Patient carries nothing to
        # derive a pseudo-ID from. The service refuses rather than skipping the check.
        for path, body in (
            ("/v1/interceptor/verify", {"patient_pseudo_id": PID, "fhir_bundle": EXAMPLE_BUNDLE_WITHOUT_IDENTIFIER,
                                        "claims": []}),
            ("/v1/chat/completions/intercept",
             chat_body() | {"clinical_context": chat_body()["clinical_context"]
                            | {"fhir_bundle": EXAMPLE_BUNDLE_WITHOUT_IDENTIFIER}}),
        ):
            with self.subTest(path=path):
                r = self.client.post(path, json=body, headers=self.headers)
                self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "INTERCEPT_BUNDLE_INVALID"))

    def test_the_audit_example_claims_do_not_belong_to_the_note_it_names(self):
        # The example's claim IDs are "illustrative" (API.md 5.1.4) and its offsets are hand-computed, so they cannot
        # match a real note. The service checks the derivation and refuses them.
        note = self.decompose().json()
        example_claim = {
            "claim_id": "clm_000000000001", "note_id": note["note_id"],
            "source_sentence": "Patient denies known drug allergies.", "source_sentence_start": 48,
            "source_sentence_end": 85, "claim_text": "Patient denies known drug allergies", "char_start": 48,
            "char_end": 84, "entities": []}
        body = {"note_id": note["note_id"], "patient_pseudo_id": PID, "verify_trace_ids": [],
                "claims": [example_claim], "results": [result_for(example_claim)]}
        r = self.client.post("/v1/interceptor/audit", json=body, headers=self.idem())
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "AUDIT_CLAIM_MISMATCH"))

    def test_the_example_redacted_spans_are_off_by_the_trailing_space(self):
        r = self.client.post("/v1/chat/completions/intercept", json=chat_body(), headers=self.headers)
        spans = [(s["doc_char_start"], s["doc_char_end"]) for s in r.json()["interception"]["redacted_spans"]]
        self.assertNotEqual(spans, [(0, 37), (64, 106)])  # API.md 4.1.3
        self.assertEqual(spans, [(0, 36), (63, 108)])  # the real sentence offsets in that upstream text


class StreamingShapeTests(ContractBase):
    def test_streaming_chunks_are_openai_shaped(self):
        from tests.support_proxy import read_sse

        with self.client.stream("POST", "/v1/chat/completions/intercept", json=chat_body(stream=True),
                                headers=self.headers) as response:
            events = read_sse(response)
        chunks = [d for n, d in events if n == "message" and d != "[DONE]"]
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertEqual(chunk["object"], "chat.completion.chunk")
            self.assertEqual(set(chunk), {"id", "object", "created", "model", "choices"})
            self.assertEqual(chunk["choices"][0]["index"], 0)
        types = {d["type"] for n, d in events if n == "interception"}
        self.assertEqual(types, {"sentence.verified", "stream.complete"})  # the closed set of interception events

    def test_trailers_are_not_sent_and_the_time_is_in_stream_complete(self):
        # Gate G4 (PLAN 7.5.3) is open. uvicorn can send trailers only over HTTP/2 to clients that ask for them, and
        # OpenAI-style clients use HTTP/1.1. Until the decision is made, the total time is in stream.complete.
        from tests.support_proxy import read_sse

        with self.client.stream("POST", "/v1/chat/completions/intercept", json=chat_body(stream=True),
                                headers=self.headers) as response:
            events = read_sse(response)
            self.assertNotIn("X-Interception-Time-MS", response.headers)
        complete = next(d for n, d in events if n == "interception" and d["type"] == "stream.complete")
        self.assertIsInstance(complete["interception_time_ms"], int)


if __name__ == "__main__":
    unittest.main()
