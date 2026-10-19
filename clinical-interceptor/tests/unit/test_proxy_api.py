import dataclasses
import hashlib
import json
import unittest

import httpx

from tests.support import auth_headers
from tests.support_bundle import BUNDLE
from tests.support_proxy import MODEL, OUTPUT, REDACTED_OUTPUT, MockUpstream, chat_body, make_app
from tests.support_verify import FakeNli

URL = "/v1/chat/completions/intercept"
NEUTRAL = {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1}


def post(client, body=None, headers=None):
    return client.post(URL, json=body or chat_body(), headers=headers or auth_headers())


def interception(response):
    return response.json()["interception"]


class ProxyBase(unittest.TestCase):
    def make_store(self):
        from interceptor.store.records import MemoryAuditStore

        return MemoryAuditStore()

    def make(self, **kwargs):
        kwargs.setdefault("store", self.make_store())
        self.client, self.upstream, self.store = make_app(**kwargs)
        return self.client


class SpecExampleTests(ProxyBase):
    """API.md 4.1.3: the model denies a penicillin allergy and orders amoxicillin. Both sentences are CRITICAL."""

    def setUp(self):
        self.make()
        self.response = post(self.client)
        self.body = self.response.json()

    def test_status_content_and_finish_reason(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertEqual(self.body["object"], "chat.completion")
        self.assertEqual(self.body["model"], MODEL)  # the logical name, never the provider's
        self.assertEqual(self.body["choices"][0]["message"]["content"], REDACTED_OUTPUT)
        self.assertEqual(self.body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(interception(self.response)["status"], "REDACTED")

    def test_redacted_spans_are_the_sentence_offsets_in_the_upstream_text(self):
        # API.md shows [0,37] and [64,106]. Those do not match this text: the sentences are [0,36] and [63,108].
        spans = interception(self.response)["redacted_spans"]
        self.assertEqual([(s["doc_char_start"], s["doc_char_end"]) for s in spans],
                         [(0, 36), (OUTPUT.index("Started"), len(OUTPUT))])
        for span in spans:
            self.assertEqual(span["risk_severity"], "CRITICAL")
            self.assertRegex(span["claim_id"], r"^clm_[0-9a-f]{12}$")
            self.assertTrue(span["reason"])

    def test_events_carry_server_set_categories_and_rules(self):
        events = interception(self.response)["events"]
        self.assertEqual(sorted((e["category"], e["risk_rule"]) for e in events),
                         [("DRUG_ALLERGY_CONFLICT", "contradiction_high_risk"),
                          ("DRUG_ALLERGY_CONFLICT", "drug_allergy_conflict")])
        self.assertTrue(all(e["event_id"] for e in events))
        self.assertEqual(interception(self.response)["overall_risk_severity"], "CRITICAL")

    def test_headers(self):
        self.assertEqual(self.response.headers["X-Interception-Status"], "REDACTED")
        self.assertEqual(self.response.headers["X-Trace-Id"], interception(self.response)["trace_id"])
        self.assertGreaterEqual(int(self.response.headers["X-Upstream-Time-MS"]), 0)
        self.assertGreaterEqual(int(self.response.headers["X-Interception-Time-MS"]), 0)
        self.assertEqual(self.response.headers["X-Interceptor-Version"], "1.0.0")

    def test_upstream_usage_and_id_pass_through(self):
        self.assertEqual(self.body["usage"]["total_tokens"], 853)
        self.assertEqual(self.body["id"], "chatcmpl-synth-0001")

    def test_the_audit_record_is_written_before_the_response(self):
        request = self.store.requests[0]
        self.assertEqual(request.pipeline_status, "RELEASED")
        self.assertEqual(request.note_sha256, hashlib.sha256(OUTPUT.encode()).hexdigest())
        self.assertEqual([s.stage for s in request.stages],
                         ["INGEST", "RETRIEVE", "EXTRACT", "VERIFY", "ATTRIBUTE", "INTERCEPT"])
        self.assertEqual(len(request.events), 2)

    def test_stored_records_hold_no_model_output(self):
        stored = json.dumps([dataclasses.asdict(r) for r in self.store.requests], default=str)
        for fragment in ("denies", "amoxicillin", "sinusitis", "HbA1c 8.2"):
            self.assertNotIn(fragment, stored)

    def test_the_fhir_bundle_and_policy_are_never_sent_upstream(self):
        sent = self.upstream.requests[0]
        self.assertEqual(sent["payload"]["model"], "gpt-test")  # routed from the logical name
        for key in ("clinical_context", "interception", "fhir_bundle"):
            self.assertNotIn(key, sent["payload"])
        self.assertNotIn("Allergy to penicillin", json.dumps(sent["payload"]))
        self.assertEqual(sent["headers"]["authorization"], "Bearer upstream-secret-key")
        self.assertTrue(sent["url"].endswith("/v1/chat/completions"))


class ReleaseDecisionTests(ProxyBase):
    def run_with(self, text, policy=None, nli=None, finish="stop", **kwargs):
        self.make(upstream=MockUpstream(text=text, finish=finish), nli_model=nli, **kwargs)
        return post(self.client, chat_body(interception=policy) if policy else chat_body())

    def test_all_safe_output_is_released_unchanged(self):
        r = self.run_with("HbA1c 8.2% on 2026-09-02.", finish="length")
        self.assertEqual(interception(r)["status"], "RELEASED")
        self.assertEqual(r.json()["choices"][0]["message"]["content"], "HbA1c 8.2% on 2026-09-02.")
        self.assertEqual(r.json()["choices"][0]["finish_reason"], "length")  # the upstream value
        self.assertEqual(interception(r)["events"], [])

    def test_a_warning_is_annotated_by_default_so_the_text_is_released_with_an_event(self):
        r = self.run_with("Started lisinopril 10 mg daily.")
        self.assertEqual(interception(r)["status"], "RELEASED")
        self.assertEqual(r.json()["choices"][0]["message"]["content"], "Started lisinopril 10 mg daily.")
        self.assertEqual([e["risk_severity"] for e in interception(r)["events"]], ["WARNING"])
        self.assertEqual(interception(r)["events"][0]["category"], "UNSUPPORTED_CLAIM")

    def test_a_warning_is_redacted_when_the_policy_says_so(self):
        r = self.run_with("Started lisinopril 10 mg daily.", {"warning_action": "REDACT_SENTENCE"})
        self.assertEqual(interception(r)["status"], "REDACTED")
        self.assertEqual(r.json()["choices"][0]["message"]["content"], "[REDACTED: WARNING]")

    def test_block_response_withholds_everything_with_200_and_content_filter(self):
        r = self.run_with(OUTPUT, {"critical_action": "BLOCK_RESPONSE"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(interception(r)["status"], "BLOCKED")
        self.assertIsNone(r.json()["choices"][0]["message"]["content"])
        self.assertEqual(r.json()["choices"][0]["finish_reason"], "content_filter")
        self.assertTrue(interception(r)["reason"])
        self.assertEqual(r.headers["X-Interception-Status"], "BLOCKED")

    def test_unverifiable_output_is_blocked_and_never_reported_as_safe(self):
        r = self.run_with("Blood pressure is stable.")
        self.assertEqual(interception(r)["status"], "BLOCKED")
        self.assertIsNone(r.json()["choices"][0]["message"]["content"])
        self.assertIn("could not be verified", interception(r)["reason"])
        self.assertNotEqual(interception(r)["overall_risk_severity"], "SAFE")
        self.assertEqual(self.store.requests[0].pipeline_status, "BLOCKED")  # a blocked response is still audited

    def test_one_unverifiable_sentence_blocks_the_whole_response(self):
        r = self.run_with("HbA1c 8.2% on 2026-09-02. Blood pressure is stable.")
        self.assertEqual(interception(r)["status"], "BLOCKED")
        self.assertIsNone(r.json()["choices"][0]["message"]["content"])

    def test_empty_output_is_blocked(self):
        r = self.run_with("")
        self.assertEqual(interception(r)["status"], "BLOCKED")
        self.assertIn("no text", interception(r)["reason"])

    def test_a_slow_verifier_times_out_and_blocks(self):
        slow = FakeNli(default=NEUTRAL, delay=0.6)
        r = self.run_with("Blood pressure is stable.", {"max_sentence_wait_ms": 200}, nli=slow)
        self.assertEqual(interception(r)["status"], "BLOCKED")

    def test_a_model_that_resolves_the_claim_lets_it_through_as_a_warning(self):
        r = self.run_with("Blood pressure is stable.", nli=FakeNli(default=NEUTRAL))
        self.assertEqual(interception(r)["status"], "RELEASED")
        self.assertEqual(interception(r)["events"][0]["risk_rule"], "unsupported_clinical")

    def test_include_claims_returns_claims_and_results(self):
        r = self.run_with(OUTPUT, {"include_claims": True})
        claims, results = interception(r)["claims"], interception(r)["results"]
        self.assertEqual(len(claims), 3)
        self.assertEqual({c["claim_id"] for c in claims}, {x["claim_id"] for x in results})
        self.assertTrue(all(c["note_id"] == interception(r)["note_id"] for c in claims))
        self.assertTrue(any(e["code"] == "723" for c in claims for e in c["entities"]))

    def test_claims_are_left_out_by_default(self):
        r = self.run_with(OUTPUT)
        self.assertEqual((interception(r)["claims"], interception(r)["results"]), ([], []))

    def test_context_warnings_are_reported(self):
        bundle = dict(BUNDLE)
        bundle["entry"] = BUNDLE["entry"] + [{"resource": {"resourceType": "MedicationRequest", "id": "mr-1"}}]
        self.make()
        body = chat_body()
        body["clinical_context"] = body["clinical_context"] | {"fhir_bundle": bundle}
        r = post(self.client, body)
        self.assertEqual([w["code"] for w in interception(r)["warnings"]], ["INTERCEPT_UNSUPPORTED_RESOURCE"])


class RejectedBeforeUpstreamTests(ProxyBase):
    def setUp(self):
        self.make()

    def assert_error(self, response, status, code, param=None):
        self.assertEqual(response.status_code, status, response.text)
        error = response.json()["error"]
        self.assertEqual(error["code"], code)
        if param:
            self.assertEqual(error["param"], param)
        self.assertEqual(self.upstream.requests, [])  # nothing was forwarded

    def test_unknown_model(self):
        self.assert_error(post(self.client, chat_body(model="some-other-model")), 400, "VALIDATION_FAILED", "model")

    def test_missing_clinical_context(self):
        body = chat_body()
        del body["clinical_context"]
        self.assert_error(post(self.client, body), 400, "VALIDATION_FAILED", "clinical_context")

    def test_patient_mismatch(self):
        body = chat_body()
        body["clinical_context"]["patient_pseudo_id"] = "pid_" + "0" * 64
        self.assert_error(post(self.client, body), 422, "INTERCEPT_PATIENT_MISMATCH",
                          "clinical_context.patient_pseudo_id")

    def test_invalid_bundle(self):
        body = chat_body()
        body["clinical_context"]["fhir_bundle"] = BUNDLE | {"type": "searchset"}
        self.assert_error(post(self.client, body), 422, "INTERCEPT_BUNDLE_INVALID", "clinical_context.fhir_bundle.type")

    def test_message_limits(self):
        self.assert_error(post(self.client, chat_body(messages=[])), 400, "VALIDATION_FAILED")
        too_many = [{"role": "user", "content": "x"}] * 101
        self.assert_error(post(self.client, chat_body(messages=too_many)), 400, "VALIDATION_FAILED")

    def test_bad_role_and_unknown_field(self):
        self.assert_error(post(self.client, chat_body(messages=[{"role": "tool", "content": "x"}])), 400,
                          "VALIDATION_FAILED")
        self.assert_error(post(self.client, chat_body(extra_field=1)), 400, "VALIDATION_FAILED")

    def test_out_of_range_policy_values(self):
        self.assert_error(post(self.client, chat_body(interception={"max_sentence_wait_ms": 5})), 400,
                          "VALIDATION_FAILED")
        self.assert_error(post(self.client, chat_body(interception={"min_confidence_entailment": 0.2})), 400,
                          "VALIDATION_FAILED")

    def test_no_upstream_configured_refuses(self):
        self.make(configured=False)
        r = post(self.client)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (502, "INTERCEPT_UPSTREAM_ERROR"))


class UpstreamFailureTests(ProxyBase):
    def run_with(self, **upstream_kwargs):
        self.make(upstream=MockUpstream(**upstream_kwargs))
        return post(self.client)

    def assert_no_content(self, response, status, code):
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (status, code))
        self.assertEqual(response.json()["error"]["clinical_impact"], "BLOCKED")
        self.assertNotIn("choices", response.json())
        self.assertEqual(self.store.requests, [])

    def test_upstream_error_is_502_and_hides_the_upstream_body(self):
        r = self.run_with(status=500)
        self.assert_no_content(r, 502, "INTERCEPT_UPSTREAM_ERROR")
        self.assertNotIn("SECRET", r.text)

    def test_upstream_timeout_is_504_and_retryable(self):
        r = self.run_with(raises=httpx.ReadTimeout("slow"))
        self.assert_no_content(r, 504, "INTERCEPT_UPSTREAM_TIMEOUT")
        self.assertTrue(r.json()["error"]["retryable"])

    def test_connection_failure_is_502(self):
        self.assert_no_content(self.run_with(raises=httpx.ConnectError("down")), 502, "INTERCEPT_UPSTREAM_ERROR")

    def test_malformed_upstream_response_is_502(self):
        self.assert_no_content(self.run_with(body=b"not json"), 502, "INTERCEPT_UPSTREAM_ERROR")

    def test_upstream_response_without_text_is_502(self):
        self.assert_no_content(self.run_with(body=b'{"choices": [{"message": {"content": null}}]}'), 502,
                               "INTERCEPT_UPSTREAM_ERROR")


class AuditFailClosedTests(ProxyBase):
    def test_audit_failure_returns_503_and_no_content(self):
        from interceptor.store.records import MemoryAuditStore

        self.make(store=MemoryAuditStore(fail=True))
        r = post(self.client)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "AUDIT_WRITE_FAILED"))
        self.assertNotIn("choices", r.json())
        self.assertNotIn("REDACTED", r.text)
        self.assertNotIn("HbA1c", r.text)  # none of the upstream text is in the error

    def test_audit_failure_blocks_even_an_all_safe_output(self):
        from interceptor.store.records import MemoryAuditStore

        self.make(upstream=MockUpstream(text="HbA1c 8.2% on 2026-09-02."), store=MemoryAuditStore(fail=True))
        r = post(self.client)
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("choices", r.json())


class ProxyAuthTests(ProxyBase):
    def setUp(self):
        self.make()

    def test_needs_the_intercept_scope(self):
        r = post(self.client, headers=auth_headers("interceptor:verify"))
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "AUTH_SCOPE_INSUFFICIENT"))
        self.assertEqual(self.upstream.requests, [])

    def test_needs_a_certificate(self):
        from tests.support import make_token

        r = post(self.client, headers={"Authorization": f"Bearer {make_token()}"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "AUTH_MTLS_REQUIRED"))
        self.assertEqual(self.upstream.requests, [])

    def test_needs_a_token(self):
        from tests.support import mtls_headers

        r = post(self.client, headers=mtls_headers())
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
