import hashlib
import unittest

import httpx

from interceptor.core.errors import ErrorClass, PipelineError
from interceptor.store.records import MemoryAuditStore
from tests.support import auth_headers
from tests.support_proxy import OUTPUT, REDACTED_OUTPUT, MockUpstream, chat_body, make_app, read_sse
from tests.support_verify import FakeNli

URL = "/v1/chat/completions/intercept"
NEUTRAL = {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1}


class ControlledStore(MemoryAuditStore):
    """Memory store that fails on chosen streaming calls."""

    def __init__(self, fail_begin=False, fail_sentence_at=None, fail_finish=False):
        super().__init__()
        self.fail_begin, self.fail_sentence_at, self.fail_finish = fail_begin, fail_sentence_at, fail_finish
        self.sentence_calls = 0

    def stream_begin(self, **kwargs):
        if self.fail_begin:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, "injected")
        return super().stream_begin(**kwargs)

    def stream_sentence(self, **kwargs):
        self.sentence_calls += 1
        if self.fail_sentence_at == self.sentence_calls:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, "injected")
        return super().stream_sentence(**kwargs)

    def stream_finish(self, **kwargs):
        if self.fail_finish:
            raise PipelineError(ErrorClass.AUDIT_WRITE_FAILURE, "injected")
        return super().stream_finish(**kwargs)


class BrokenBody(httpx.AsyncByteStream):
    """An upstream body that sends one event and then breaks."""

    def __init__(self, first):
        self.first = first

    async def __aiter__(self):
        yield self.first
        raise httpx.ReadError("connection reset")


def stream_body(**changes):
    return chat_body(stream=True, **changes)


class StreamBase(unittest.TestCase):
    def make_store(self):
        return MemoryAuditStore()

    def go(self, text=OUTPUT, body=None, upstream=None, store=None, nli=None, **mock_kwargs):
        mock = upstream or MockUpstream(text=text, **mock_kwargs)
        self.client, self.upstream, self.store = make_app(upstream=mock, store=store or self.make_store(), nli_model=nli)
        with self.client.stream("POST", URL, json=body or stream_body(), headers=auth_headers()) as response:
            self.response = response
            if response.headers["content-type"].startswith("text/event-stream"):
                self.events = read_sse(response)
            else:
                response.read()
                self.events = []
        return self.events

    # ---- reading the event list ----
    def contents(self):
        return [e[1]["choices"][0]["delta"]["content"] for e in self.events
                if e[0] == "message" and e[1] != "[DONE]" and "content" in e[1]["choices"][0]["delta"]]

    def interceptions(self, kind=None):
        return [e[1] for e in self.events if e[0] == "interception" and (kind is None or e[1]["type"] == kind)]

    def errors(self):
        return [e[1]["error"] for e in self.events if e[0] == "error"]

    def finish_reasons(self):
        return [e[1]["choices"][0]["finish_reason"] for e in self.events
                if e[0] == "message" and e[1] != "[DONE]" and e[1]["choices"][0]["finish_reason"]]

    def complete(self):
        return self.interceptions("stream.complete")[0]

    def assert_failed_closed(self, code):
        self.assertEqual([e["code"] for e in self.errors()], [code])
        self.assertEqual(self.errors()[0]["clinical_impact"], "BLOCKED")
        self.assertEqual(self.events[-1], ("message", "[DONE]"))  # [DONE] follows the error, nothing after it
        self.assertEqual(self.events[-2][0], "error")
        self.assertEqual(self.finish_reasons(), [])  # never "stop" after an error (API.md 4.3 rule 4)
        self.assertEqual(self.interceptions("stream.complete"), [])

    # ---- reading what was audited (works on either store) ----
    def _is_memory(self):
        return isinstance(self.store, MemoryAuditStore)

    def _rows(self, sql):
        with self.store._connect() as conn:
            return conn.execute(sql).fetchall()

    def stages(self):
        if self._is_memory():
            return [t["stage"] for t in self.store.traces]
        return [r[0] for r in self._rows("SELECT stage FROM audit_traces ORDER BY created_at, trace_id")]

    def note(self):
        if self._is_memory():
            return next(iter(self.store.notes.values()))
        keys = ("pipeline_status", "claim_count", "char_length", "note_sha256")
        row = self._rows("SELECT pipeline_status, claim_count, char_length, note_sha256 FROM intercepted_notes")[0]
        note = dict(zip(keys, row))
        note["note_sha256"] = note["note_sha256"].strip()
        return note

    def note_count(self):
        if self._is_memory():
            return len(self.store.notes)
        return self._rows("SELECT count(*) FROM intercepted_notes")[0][0]

    def event_count(self):
        if self._is_memory():
            return len(self.store.events)
        return self._rows("SELECT count(*) FROM hallucination_events")[0][0]

    def dump_audit(self):
        if self._is_memory():
            return str([t["payload"] for t in self.store.traces]) + str(self.store.events)
        return " ".join(r[0] for r in self._rows("SELECT output_payload::text FROM audit_traces")) + " ".join(
            r[0] for r in self._rows("SELECT row_to_json(e)::text FROM hallucination_events e"))


class SpecStreamTests(StreamBase):
    def test_event_order(self):
        self.go()
        shape = [(name, data["type"] if name == "interception" else "chunk" if data != "[DONE]" else "done")
                 for name, data in self.events]
        self.assertEqual(shape, [
            ("message", "chunk"),  # role
            ("interception", "sentence.verified"), ("message", "chunk"),
            ("interception", "sentence.verified"), ("message", "chunk"),
            ("interception", "sentence.verified"), ("message", "chunk"),
            ("message", "chunk"),  # finish_reason
            ("interception", "stream.complete"),
            ("message", "done"),
        ])
        self.assertEqual(self.events[0][1]["choices"][0]["delta"], {"role": "assistant"})

    def test_a_sentence_verdict_always_comes_before_its_text(self):
        self.go()
        for index, (name, data) in enumerate(self.events):
            if name == "message" and data != "[DONE]" and "content" in data["choices"][0]["delta"]:
                self.assertEqual(self.events[index - 1][1]["type"], "sentence.verified")

    def test_streamed_text_equals_the_non_streaming_result(self):
        self.go()
        self.assertEqual("".join(self.contents()), REDACTED_OUTPUT)
        self.assertEqual(self.finish_reasons(), ["stop"])

    def test_sentence_events(self):
        self.go()
        verdicts = self.interceptions("sentence.verified")
        self.assertEqual([(v["sentence_index"], v["doc_char_start"], v["doc_char_end"]) for v in verdicts],
                         [(0, 0, 36), (1, 37, 62), (2, 63, 108)])
        self.assertEqual([(v["risk_severity"], v["action"]) for v in verdicts],
                         [("CRITICAL", "REDACTED"), ("SAFE", "RELEASED"), ("CRITICAL", "REDACTED")])
        self.assertTrue(all(len(v["claim_ids"]) == 1 for v in verdicts))

    def test_stream_complete(self):
        self.go()
        done = self.complete()
        self.assertEqual((done["status"], done["overall_risk_severity"]), ("REDACTED", "CRITICAL"))
        self.assertIsInstance(done["interception_time_ms"], int)
        self.assertTrue(done["note_id"] and done["trace_id"])

    def test_response_headers(self):
        self.go()
        self.assertTrue(self.response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(self.response.headers["Cache-Control"], "no-cache")
        self.assertEqual(self.response.headers["X-Interceptor-Version"], "1.0.0")
        self.assertIn("X-Request-Id", self.response.headers)
        # the total time is only known at the end, so it is in stream.complete and not in a header
        self.assertNotIn("X-Interception-Time-MS", self.response.headers)

    def test_delta_size_does_not_change_the_result(self):
        for piece in (1, 4, 17, 500):
            with self.subTest(piece=piece):
                self.go(stream_piece=piece)
                self.assertEqual("".join(self.contents()), REDACTED_OUTPUT)

    def test_headers_and_line_breaks_between_sentences_are_kept(self):
        text = "Labs: HbA1c 8.2% on 2026-09-02.\nHbA1c 8.2% on 2026-09-02."
        self.go(text=text)
        self.assertEqual("".join(self.contents()), text)
        self.assertEqual(self.complete()["status"], "RELEASED")

    def test_a_warning_is_annotated_and_released(self):
        self.go(text="Started lisinopril 10 mg daily.")
        self.assertEqual("".join(self.contents()), "Started lisinopril 10 mg daily.")
        verdict = self.interceptions("sentence.verified")[0]
        self.assertEqual((verdict["risk_severity"], verdict["action"]), ("WARNING", "ANNOTATED"))
        self.assertEqual(self.complete()["status"], "RELEASED")

    def test_upstream_finish_reason_is_kept_when_nothing_was_changed(self):
        self.go(text="HbA1c 8.2% on 2026-09-02.", finish="length")
        self.assertEqual(self.finish_reasons(), ["length"])


class StreamAuditTests(StreamBase):
    def test_every_sentence_is_audited_and_the_note_is_finalized(self):
        self.go()
        self.assertEqual(self.stages().count("VERIFY"), 3)
        self.assertEqual(self.stages()[-1], "INTERCEPT")
        note = self.note()
        self.assertEqual((note["pipeline_status"], note["claim_count"], note["char_length"]), ("RELEASED", 3, len(OUTPUT)))
        self.assertEqual(note["note_sha256"], hashlib.sha256(OUTPUT.encode()).hexdigest())
        self.assertEqual(self.event_count(), 2)

    def test_audit_rows_hold_no_model_output(self):
        self.go()
        stored = self.dump_audit()
        for fragment in ("denies", "amoxicillin", "sinusitis"):
            self.assertNotIn(fragment, stored)

    def test_audit_failure_before_the_first_sentence_releases_nothing(self):
        self.go(store=ControlledStore(fail_sentence_at=1))
        self.assertEqual(self.contents(), [])
        self.assert_failed_closed("AUDIT_WRITE_FAILED")

    def test_audit_failure_mid_stream_stops_after_the_sentences_already_audited(self):
        self.go(store=ControlledStore(fail_sentence_at=2))
        self.assertEqual(self.contents(), ["[REDACTED: CRITICAL]"])  # only the sentence that was audited
        self.assert_failed_closed("AUDIT_WRITE_FAILED")
        self.assertEqual(self.store.sentence_calls, 2)

    def test_if_the_audit_cannot_start_the_request_is_a_json_503_with_no_stream(self):
        self.go(store=ControlledStore(fail_begin=True))
        self.assertEqual(self.response.status_code, 503)
        self.assertTrue(self.response.headers["content-type"].startswith("application/json"))
        self.assertEqual(self.events, [])
        self.assertEqual(self.response.json()["error"]["code"], "AUDIT_WRITE_FAILED")
        self.assertEqual(len(self.upstream.requests), 1)  # upstream was asked first, then abandoned

    def test_failure_to_finalize_the_audit_tells_the_client_to_discard(self):
        self.go(store=ControlledStore(fail_finish=True))
        self.assertEqual("".join(self.contents()), REDACTED_OUTPUT)  # already released, already audited
        self.assert_failed_closed("AUDIT_WRITE_FAILED")


class StreamFailClosedTests(StreamBase):
    def test_a_slow_sentence_is_withheld_and_the_stream_continues(self):
        text = "Patient denies known drug allergies. Blood pressure is stable. HbA1c 8.2% on 2026-09-02."
        body = stream_body(interception={"max_sentence_wait_ms": 200})
        self.go(text=text, body=body, nli=FakeNli(default=NEUTRAL, delay=0.6))
        self.assertEqual("".join(self.contents()),
                         "[REDACTED: CRITICAL] [WITHHELD: VERIFICATION_TIMEOUT] HbA1c 8.2% on 2026-09-02.")
        verdicts = self.interceptions("sentence.verified")
        self.assertEqual(verdicts[1]["action"], "REDACTED")  # the closed action set has no WITHHELD
        done = self.complete()
        self.assertEqual(done["status"], "REDACTED")
        self.assertEqual(self.finish_reasons(), ["stop"])
        self.assertEqual(self.errors(), [])

    def test_an_unverifiable_sentence_ends_the_stream_with_an_error_after_the_verified_ones(self):
        self.go(text="HbA1c 8.2% on 2026-09-02. Blood pressure is stable.")
        self.assertEqual("".join(self.contents()), "HbA1c 8.2% on 2026-09-02.")
        self.assert_failed_closed("INTERCEPT_VERIFIER_UNAVAILABLE")
        self.assertEqual(self.note()["pipeline_status"], "BLOCKED")

    def test_an_unverifiable_first_sentence_releases_no_content_at_all(self):
        self.go(text="Blood pressure is stable.")
        self.assertEqual(self.contents(), [])
        self.assert_failed_closed("INTERCEPT_VERIFIER_UNAVAILABLE")

    def test_block_response_stops_the_stream_with_status_blocked_and_content_filter(self):
        text = "HbA1c 8.2% on 2026-09-02. Patient denies known drug allergies. HbA1c 8.2% on 2026-09-02."
        self.go(text=text, body=stream_body(interception={"critical_action": "BLOCK_RESPONSE"}))
        self.assertEqual("".join(self.contents()), "HbA1c 8.2% on 2026-09-02.")  # nothing after the critical sentence
        self.assertEqual(self.finish_reasons(), ["content_filter"])
        self.assertEqual(self.complete()["status"], "BLOCKED")
        self.assertEqual(self.note()["pipeline_status"], "BLOCKED")
        self.assertEqual(len(self.interceptions("sentence.verified")), 1)

    def test_upstream_http_error_before_the_first_event_is_a_json_error(self):
        self.go(status=500)
        self.assertEqual(self.response.status_code, 502)
        self.assertTrue(self.response.headers["content-type"].startswith("application/json"))
        self.assertEqual(self.response.json()["error"]["code"], "INTERCEPT_UPSTREAM_ERROR")
        self.assertEqual(self.note_count(), 0)  # nothing was started

    def test_upstream_timeout_before_the_first_event_is_a_504(self):
        self.go(raises=httpx.ReadTimeout("slow"))
        self.assertEqual((self.response.status_code, self.response.json()["error"]["code"]),
                         (504, "INTERCEPT_UPSTREAM_TIMEOUT"))

    def test_upstream_failure_mid_stream_is_an_error_event_and_the_note_is_marked_failed(self):
        first = (b'data: {"choices": [{"delta": {"content": "HbA1c 8.2% on 2026-09-02. HbA1c "},'
                 b'"finish_reason": null}]}\n\n')

        def handler(request):
            return httpx.Response(200, stream=BrokenBody(first), headers={"content-type": "text/event-stream"})

        mock = MockUpstream()
        mock.handler = handler
        self.go(upstream=mock)
        self.assertEqual("".join(self.contents()), "HbA1c 8.2% on 2026-09-02.")
        self.assert_failed_closed("INTERCEPT_UPSTREAM_ERROR")
        self.assertEqual(self.note()["pipeline_status"], "FAILED")

    def test_an_internal_fault_mid_stream_is_an_error_event_not_a_crash(self):
        from unittest import mock

        with mock.patch("interceptor.intercept.stream.verify_unit", side_effect=RuntimeError("bug")):
            self.go()
        self.assert_failed_closed("INTERCEPT_INTERNAL_ERROR")
        self.assertEqual(self.contents(), [])


class StreamRejectionTests(StreamBase):
    def test_a_stream_request_is_rejected_before_the_upstream_like_any_other(self):
        body = stream_body()
        body["clinical_context"]["patient_pseudo_id"] = "pid_" + "0" * 64
        self.go(body=body)
        self.assertEqual(self.response.status_code, 422)
        self.assertEqual(self.upstream.requests, [])

    def test_the_stream_flag_is_forwarded_upstream(self):
        self.go()
        self.assertTrue(self.upstream.requests[0]["payload"]["stream"])


if __name__ == "__main__":
    unittest.main()
