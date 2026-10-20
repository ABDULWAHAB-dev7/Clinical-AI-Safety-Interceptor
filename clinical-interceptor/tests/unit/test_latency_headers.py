"""Latency reporting after the G4 amendment (docs/gates/G4-http-trailers.md, API 4.5).

No latency value is sent as an HTTP trailer. On streams the values are in the stream.complete event. On non-streaming
responses X-Interception-Time-MS is a header.
"""

import unittest

from tests.support import auth_headers
from tests.support_proxy import OUTPUT, MockUpstream, chat_body, make_app, read_sse

URL = "/v1/chat/completions/intercept"


class StreamLatencyTests(unittest.TestCase):
    def setUp(self):
        client, _, _ = make_app(upstream=MockUpstream(text=OUTPUT))
        with client.stream("POST", URL, json=chat_body(stream=True), headers=auth_headers()) as response:
            self.headers = dict(response.headers)
            self.trailers_expected = response.headers.get("trailer")
            self.events = read_sse(response)

    def complete(self):
        return next(data for name, data in self.events if name == "interception" and data["type"] == "stream.complete")

    def test_stream_has_no_time_header(self):
        self.assertNotIn("x-interception-time-ms", self.headers)
        self.assertNotIn("x-interception-first-release-ms", self.headers)

    def test_stream_announces_no_trailers(self):
        self.assertIsNone(self.trailers_expected)

    def test_stream_complete_carries_both_timings(self):
        complete = self.complete()
        self.assertIsInstance(complete["interception_time_ms"], int)
        self.assertIsInstance(complete["first_release_ms"], int)
        self.assertLessEqual(complete["first_release_ms"], complete["interception_time_ms"])


class NonStreamLatencyTests(unittest.TestCase):
    def test_non_stream_response_has_the_time_header(self):
        client, _, _ = make_app(upstream=MockUpstream(text=OUTPUT))
        response = client.post(URL, json=chat_body(), headers=auth_headers())
        self.assertGreaterEqual(int(response.headers["X-Interception-Time-MS"]), 0)


if __name__ == "__main__":
    unittest.main()
