"""Quarantine list, notifications and the persisted verification failure (PRD journeys C and D)."""

import unittest
from unittest import mock

from interceptor.api import notify
from tests.support import auth_headers
from tests.unit.test_document_check import CLEAN, CONTRADICTED, NEEDS_NLI, app_client


class Recorder:
    def __init__(self):
        self.events = []

    def notify(self, event):
        self.events.append(event)


class Broken:
    def notify(self, event):
        raise RuntimeError("webhook down")


class _Failing:
    def score(self, premise, hypothesis):
        raise TimeoutError("model unavailable")


def quarantine(client, headers=None):
    return client.get("/v1/interceptor/quarantine", headers=headers or auth_headers())


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.client, self.store = app_client()
        self.recorder = Recorder()
        self.client.app.state.notifier = self.recorder

    def test_a_blocked_note_is_listed_and_the_team_is_told_without_any_text(self):
        blocked = self.client.post("/v1/verify", json=CONTRADICTED, headers=auth_headers()).json()
        listed = quarantine(self.client).json()
        self.assertEqual([n["note_id"] for n in listed["notes"]], [blocked["note_id"]])
        self.assertEqual(len(self.recorder.events), 1)
        event = self.recorder.events[0]
        self.assertEqual(event["event"], notify.NOTE_QUARANTINED)
        self.assertEqual(event["report_id"], blocked["request_id"])
        self.assertGreaterEqual(event["critical_count"], 1)
        self.assertNotIn(CONTRADICTED["document_text"].split(".")[0], repr(event))

    def test_a_clean_note_is_not_listed_and_nobody_is_told(self):
        self.client.post("/v1/verify", json=CLEAN, headers=auth_headers())
        self.assertEqual(quarantine(self.client).json(), {"count": 0, "notes": []})
        self.assertEqual(self.recorder.events, [])

    def test_an_overridden_note_leaves_the_quarantine(self):
        blocked = self.client.post("/v1/verify", json=CONTRADICTED, headers=auth_headers()).json()
        report = self.client.get(f"/v1/interceptor/audit/reports/{blocked['request_id']}", headers=auth_headers()).json()
        critical = {e["claim_id"] for e in self.store.events if e["risk_severity"] == "CRITICAL"}
        span = next(s["span_id"] for s in report["attribution_spans"] if s.get("claim_id") in critical)
        r = self.client.post("/v1/interceptor/override", headers=auth_headers(), json={
            "report_id": blocked["request_id"], "span_id": span, "override_reason": "Chart reviewed",
            "attending_signature": "Dr. Example Attending"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(quarantine(self.client).json()["count"], 0)

    def test_a_failing_notifier_never_changes_the_verdict(self):
        self.client.app.state.notifier = Broken()
        body = self.client.post("/v1/verify", json=CONTRADICTED, headers=auth_headers()).json()
        self.assertEqual((body["outcome"], body["delivered"]), ("HARD_BLOCK", False))

    def test_the_read_scope_is_required(self):
        r = quarantine(self.client, auth_headers(scope="interceptor:verify"))
        self.assertEqual(r.status_code, 403)

    def test_an_empty_note_is_quarantined_for_r4(self):
        body = self.client.post("/v1/verify", json=CLEAN | {"document_text": "..."}, headers=auth_headers()).json()
        self.assertEqual(quarantine(self.client).json()["count"], 1)
        self.assertEqual(self.recorder.events[0]["rules"], ["R4_no_assertions"])
        self.assertEqual(body["outcome"], "HARD_BLOCK")


class VerificationUnavailableTests(unittest.TestCase):
    def test_the_note_is_marked_failed_and_the_team_is_told(self):
        client, store = app_client(nli_model=_Failing())
        recorder = Recorder()
        client.app.state.notifier = recorder
        r = client.post("/v1/verify", json=CLEAN | {"document_text": NEEDS_NLI}, headers=auth_headers())
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "INTERCEPT_VERIFIER_UNAVAILABLE")
        self.assertEqual([n["pipeline_status"] for n in store.notes.values()], ["FAILED"])
        self.assertEqual([e["event"] for e in recorder.events], [notify.VERIFICATION_UNAVAILABLE])
        self.assertNotIn("ATTRIBUTE", [t["stage"] for t in store.traces])


class WebhookTests(unittest.TestCase):
    def test_only_https_urls_are_accepted(self):
        with self.assertRaises(ValueError):
            notify.WebhookNotifier("http://example.test/hook")

    def test_a_network_error_is_swallowed_and_unlisted_fields_are_not_sent(self):
        sent = {}

        def fake_urlopen(request, timeout):
            sent["body"] = request.data
            raise OSError("down")

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            notify.WebhookNotifier("https://example.test/hook").notify(
                {"event": "X", "note_id": "n", "claim_text": "must not be sent"})
        self.assertIn(b'"note_id"', sent["body"])
        self.assertNotIn(b"claim_text", sent["body"])


if __name__ == "__main__":
    unittest.main()
