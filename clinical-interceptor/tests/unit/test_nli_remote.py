"""Tests for the remote NLI scorer: the contract, and every way the service can fail.

A remote scorer that fails open would be a hole in the fail-closed design, so most of these tests are about
failures: none of them may produce a score.
"""

import unittest

from interceptor.core.errors import ErrorClass, PipelineError
from interceptor.nli.remote import MAX_PAIRS, RemoteNliError, RemoteNliScorer, RemoteNliUnavailable, build_scorer

GOOD = {"entailment": 0.1, "neutral": 0.2, "contradiction": 0.7}


class FakeResponse:
    def __init__(self, status_code=200, body=None, raises=None):
        self.status_code = status_code
        self._body = body
        self._raises = raises

    def json(self):
        if self._raises is not None:
            raise self._raises
        return self._body


class FakeClient:
    """Records the requests and answers with a canned response."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


def scorer(response=None, error=None, **kwargs):
    # startup_retries defaults to 0 here: these tests are about a broken service, and the retry path has its own
    # tests. A test that wants the retry behaviour asks for it.
    kwargs.setdefault("startup_retries", 0)
    client = FakeClient(response=response, error=error)
    return RemoteNliScorer("http://nli.internal:8100", client=client, **kwargs), client


def ok_response(rows):
    return FakeResponse(body={"scores": rows})


class ScoreTests(unittest.TestCase):
    def test_score_returns_the_three_labels(self):
        s, client = scorer(ok_response([GOOD]))
        scores = s.score("premise", "hypothesis")
        self.assertEqual(scores, GOOD)
        self.assertEqual(client.calls[0]["url"], "http://nli.internal:8100/v1/score")
        self.assertEqual(client.calls[0]["json"], {"pairs": [["premise", "hypothesis"]]})

    def test_score_batch_sends_every_pair_in_one_request(self):
        s, client = scorer(ok_response([GOOD, GOOD, GOOD]))
        rows = s.score_batch([("a", "b"), ("c", "d"), ("e", "f")])
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["json"]["pairs"], [["a", "b"], ["c", "d"], ["e", "f"]])

    def test_score_batch_of_nothing_makes_no_request(self):
        s, client = scorer(ok_response([]))
        self.assertEqual(s.score_batch([]), [])
        self.assertEqual(client.calls, [])

    def test_the_token_is_sent_as_a_bearer_header(self):
        s, client = scorer(ok_response([GOOD]), token="secret")
        s.score("a", "b")
        self.assertEqual(client.calls[0]["headers"]["Authorization"], "Bearer secret")

    def test_no_token_means_no_authorization_header(self):
        s, client = scorer(ok_response([GOOD]))
        s.score("a", "b")
        self.assertNotIn("Authorization", client.calls[0]["headers"])


class FailureTests(unittest.TestCase):
    """Every one of these must raise. A scorer that answers with a default would let a claim through unchecked."""

    def test_a_non_200_answer_raises(self):
        s, _ = scorer(FakeResponse(status_code=500, body={}))
        with self.assertRaises(RemoteNliError):
            s.score("a", "b")

    def test_a_body_that_is_not_json_raises(self):
        s, _ = scorer(FakeResponse(body=None, raises=ValueError("not json")))
        with self.assertRaises(RemoteNliError):
            s.score("a", "b")

    def test_the_wrong_number_of_scores_raises(self):
        s, _ = scorer(ok_response([GOOD]))
        with self.assertRaises(RemoteNliError):
            s.score_batch([("a", "b"), ("c", "d")])

    def test_a_score_without_the_three_labels_raises(self):
        s, _ = scorer(ok_response([{"entailment": 0.5, "neutral": 0.5}]))
        with self.assertRaises(RemoteNliError):
            s.score("a", "b")

    def test_a_score_outside_zero_to_one_raises(self):
        s, _ = scorer(ok_response([{"entailment": 1.5, "neutral": 0.0, "contradiction": 0.0}]))
        with self.assertRaises(RemoteNliError):
            s.score("a", "b")

    def test_a_score_that_is_not_a_number_raises(self):
        s, _ = scorer(ok_response([{"entailment": "high", "neutral": 0.5, "contradiction": 0.0}]))
        with self.assertRaises(RemoteNliError):
            s.score("a", "b")

    def test_a_transport_failure_raises(self):
        # no startup retries here: this is the "the service is broken" case, not the "the service is booting" one
        s, _ = scorer(error=OSError("connection refused"), startup_retries=0)
        with self.assertRaises(RemoteNliError):
            s.score("a", "b")

    def test_a_service_that_is_booting_is_retried(self):
        """A service that scales to zero has to start before it can answer, and booting takes longer than a call."""
        attempts = []

        class FlakyClient(FakeClient):
            def post(self, url, json=None, headers=None, timeout=None):
                attempts.append(url)
                if len(attempts) < 3:
                    raise OSError("connection refused")
                return FakeResponse(body={"scores": [GOOD]})

        client = FlakyClient()
        s = RemoteNliScorer("http://nli.internal:8100", client=client, startup_retries=5, retry_backoff_seconds=0)
        self.assertEqual(s.score("a", "b"), GOOD)
        self.assertEqual(len(attempts), 3)

    def test_a_service_that_never_comes_up_gives_up(self):
        attempts = []

        class DeadClient(FakeClient):
            def post(self, url, json=None, headers=None, timeout=None):
                attempts.append(url)
                raise OSError("connection refused")

        client = DeadClient()
        s = RemoteNliScorer("http://nli.internal:8100", client=client, startup_retries=3, retry_backoff_seconds=0)
        with self.assertRaises(RemoteNliError):
            s.score("a", "b")
        self.assertEqual(len(attempts), 4)  # the first attempt and three retries

    def test_a_wrong_answer_is_not_retried(self):
        """A protocol failure is an answer, and an answer must not be second-guessed."""
        attempts = []

        class WrongClient(FakeClient):
            def post(self, url, json=None, headers=None, timeout=None):
                attempts.append(url)
                return FakeResponse(status_code=500, body={})

        client = WrongClient()
        s = RemoteNliScorer("http://nli.internal:8100", client=client, startup_retries=5, retry_backoff_seconds=0)
        with self.assertRaises(RemoteNliError):
            s.score("a", "b")
        self.assertEqual(len(attempts), 1)

    def test_too_many_pairs_raises_before_any_request(self):
        s, client = scorer(ok_response([]))
        with self.assertRaises(RemoteNliError):
            s.score_batch([("a", "b")] * (MAX_PAIRS + 1))
        self.assertEqual(client.calls, [])


class WarmUpTests(unittest.TestCase):
    def test_warm_up_passes_when_the_service_agrees_with_itself(self):
        s, _ = scorer(ok_response([{"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05}]))
        s.warm_up()  # no exception

    def test_warm_up_fails_when_the_service_is_unreachable(self):
        s, _ = scorer(error=OSError("connection refused"))
        with self.assertRaises(RemoteNliUnavailable):
            s.warm_up()

    def test_warm_up_fails_when_the_service_answers_wrongly(self):
        s, _ = scorer(ok_response([{"entailment": 0.1, "neutral": 0.1, "contradiction": 0.8}]))
        with self.assertRaises(PipelineError) as caught:
            s.warm_up()
        self.assertEqual(caught.exception.error_class, ErrorClass.POLICY_LOAD_FAILURE)


class BuildTests(unittest.TestCase):
    def test_build_scorer_needs_an_endpoint(self):
        with self.assertRaises(RuntimeError):
            build_scorer(None, timeout_seconds=5.0)

    def test_a_service_that_cannot_be_reached_does_not_stop_the_interceptor(self):
        """The interceptor's availability must not depend on the model's.

        A service that is down, or still booting, leaves the interceptor running: the claims that need the model
        fail closed and the rest of the service keeps working.
        """
        # port 1 refuses the connection at once, so the test does not wait for a timeout
        scorer = build_scorer("http://127.0.0.1:1", timeout_seconds=1.0, startup_retries=0)
        self.assertFalse(scorer.ready)

    def test_a_service_that_answers_wrongly_stops_the_interceptor(self):
        """That is a fault, not a service that is down, and a fault is a start-up failure."""
        client = FakeClient(response=FakeResponse(status_code=401, body={}))
        scorer = RemoteNliScorer("http://nli.internal:8100", client=client, startup_retries=0)
        with self.assertRaises(PipelineError) as caught:
            scorer.warm_up()
        self.assertEqual(caught.exception.error_class, ErrorClass.POLICY_LOAD_FAILURE)

    def test_the_scorer_is_ready_after_a_good_warm_up(self):
        s, _ = scorer(ok_response([{"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05}]))
        self.assertFalse(s.ready)
        s.warm_up()
        self.assertTrue(s.ready)


if __name__ == "__main__":
    unittest.main()
