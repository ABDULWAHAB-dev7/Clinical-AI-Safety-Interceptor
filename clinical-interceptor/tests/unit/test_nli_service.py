"""Tests for the NLI inference service: the routes, the token, and the refusals."""

import os
import unittest

from fastapi.testclient import TestClient

from nli_service.app import create_app

GOOD = {"entailment": 0.1, "neutral": 0.2, "contradiction": 0.7}


class FakeScorer:
    def __init__(self, error=None):
        self.model_path = "/models/nli/cross-encoder-nli-deberta-v3-base"
        self.lock_path = "/models/nli/MODEL.lock"
        self.labels = ["entailment", "neutral", "contradiction"]
        self.error = error
        self.batches = []

    @property
    def lock_verified(self):
        return True

    def score_batch(self, pairs):
        if self.error is not None:
            raise self.error
        self.batches.append(list(pairs))
        return [dict(GOOD) for _ in pairs]


class ScoreRouteTests(unittest.TestCase):
    def test_a_pair_is_scored(self):
        scorer = FakeScorer()
        with TestClient(create_app(scorer=scorer)) as client:
            response = client.post("/v1/score", json={"pairs": [["premise", "hypothesis"]]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"scores": [GOOD]})
        self.assertEqual(scorer.batches, [[("premise", "hypothesis")]])

    def test_several_pairs_are_one_batch(self):
        scorer = FakeScorer()
        with TestClient(create_app(scorer=scorer)) as client:
            response = client.post("/v1/score", json={"pairs": [["a", "b"], ["c", "d"]]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["scores"]), 2)
        self.assertEqual(len(scorer.batches), 1)

    def test_a_pair_that_is_not_two_strings_is_refused(self):
        with TestClient(create_app(scorer=FakeScorer())) as client:
            response = client.post("/v1/score", json={"pairs": [["only one"]]})
        self.assertEqual(response.status_code, 422)

    def test_an_empty_pair_is_refused(self):
        with TestClient(create_app(scorer=FakeScorer())) as client:
            response = client.post("/v1/score", json={"pairs": [["", "hypothesis"]]})
        self.assertEqual(response.status_code, 422)

    def test_an_empty_batch_is_refused(self):
        with TestClient(create_app(scorer=FakeScorer())) as client:
            response = client.post("/v1/score", json={"pairs": []})
        self.assertEqual(response.status_code, 422)

    def test_a_model_failure_answers_503_and_no_scores(self):
        with TestClient(create_app(scorer=FakeScorer(error=RuntimeError("model died")))) as client:
            response = client.post("/v1/score", json={"pairs": [["a", "b"]]})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("scores", response.json())


class HealthTests(unittest.TestCase):
    def test_health_reports_the_model_and_the_lock(self):
        with TestClient(create_app(scorer=FakeScorer())) as client:
            response = client.get("/v1/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["lock_verified"])
        self.assertEqual(body["labels"], ["entailment", "neutral", "contradiction"])


class TokenTests(unittest.TestCase):
    def setUp(self):
        os.environ["NLI_SERVICE_TOKEN"] = "service-secret"
        self.addCleanup(os.environ.pop, "NLI_SERVICE_TOKEN", None)

    def test_a_request_without_the_token_is_refused(self):
        with TestClient(create_app(scorer=FakeScorer())) as client:
            response = client.post("/v1/score", json={"pairs": [["a", "b"]]})
        self.assertEqual(response.status_code, 401)

    def test_a_request_with_the_wrong_token_is_refused(self):
        with TestClient(create_app(scorer=FakeScorer())) as client:
            response = client.post("/v1/score", json={"pairs": [["a", "b"]]}, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)

    def test_a_request_with_the_token_is_answered(self):
        with TestClient(create_app(scorer=FakeScorer())) as client:
            response = client.post("/v1/score", json={"pairs": [["a", "b"]]},
                                   headers={"Authorization": "Bearer service-secret"})
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
