"""POST /v1/interceptor/acknowledge and the review-status read (PRD journey B, 2026-10-22 acknowledge record)."""

import unittest

from tests.support import auth_headers
from tests.unit.test_document_check import CLEAN, CONTRADICTED, NEEDS_NLI, _Neutral, app_client

ACK = "/v1/interceptor/acknowledge"


def body(report_id, span_id, **changes):
    return {"report_id": report_id, "span_id": span_id, "action": "ACCEPT", "comment": "Checked against the chart",
            "clinician_signature": "Dr. Example Attending"} | changes


class AcknowledgeTests(unittest.TestCase):
    def setUp(self):
        self.client, self.store = app_client(nli_model=_Neutral())
        warned = self.client.post("/v1/verify", json=CLEAN | {"document_text": NEEDS_NLI}, headers=auth_headers()).json()
        self.report_id = warned["request_id"]
        self.span_id = warned["spans"][0]["span_id"]

    def status(self, report_id=None):
        return self.client.get(f"/v1/interceptor/audit/reports/{report_id or self.report_id}/review-status",
                               headers=auth_headers())

    def test_a_warning_is_open_until_it_is_acknowledged(self):
        before = self.status().json()
        self.assertEqual(before["open_count"], 1)
        self.assertFalse(before["ready_to_sign"])
        self.assertEqual(before["findings"][0]["resolution"], "OPEN")
        r = self.client.post(ACK, json=body(self.report_id, self.span_id), headers=auth_headers())
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["resolution"], "FALSE_POSITIVE")
        after = self.status().json()
        self.assertEqual((after["open_count"], after["ready_to_sign"]), (0, True))
        self.assertEqual(after["findings"][0]["resolution"], "FALSE_POSITIVE")

    def test_each_action_maps_to_one_resolution(self):
        for action, resolution in (("ACCEPT", "FALSE_POSITIVE"), ("EDIT", "CORRECTED"), ("REJECT", "CONFIRMED_ERROR")):
            with self.subTest(action=action):
                client, store = app_client(nli_model=_Neutral())
                warned = client.post("/v1/verify", json=CLEAN | {"document_text": NEEDS_NLI},
                                     headers=auth_headers()).json()
                r = client.post(ACK, json=body(warned["request_id"], warned["spans"][0]["span_id"], action=action),
                                headers=auth_headers())
                self.assertEqual(r.json()["resolution"], resolution)
                self.assertEqual(store.reviews[0]["resolution"], resolution)

    def test_the_comment_and_signature_are_not_stored_as_text(self):
        self.client.post(ACK, json=body(self.report_id, self.span_id), headers=auth_headers())
        stored = repr(self.store.reviews)
        self.assertNotIn("Checked against the chart", stored)
        self.assertNotIn("Dr. Example Attending", stored)
        self.assertTrue(self.store.reviews[0]["reviewer_ref"].startswith("rev_"))

    def test_the_same_finding_cannot_be_acknowledged_twice(self):
        self.assertEqual(self.client.post(ACK, json=body(self.report_id, self.span_id), headers=auth_headers()).status_code, 201)
        r = self.client.post(ACK, json=body(self.report_id, self.span_id, action="REJECT"), headers=auth_headers())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(len(self.store.reviews), 1)

    def test_a_critical_finding_needs_the_override_not_an_acknowledgment(self):
        client, store = app_client()
        blocked = client.post("/v1/verify", json=CONTRADICTED, headers=auth_headers()).json()
        report = client.get(f"/v1/interceptor/audit/reports/{blocked['request_id']}", headers=auth_headers()).json()
        critical = {e["claim_id"] for e in store.events if e["risk_severity"] == "CRITICAL"}
        span = next(s["span_id"] for s in report["attribution_spans"] if s.get("claim_id") in critical)
        r = client.post(ACK, json=body(blocked["request_id"], span), headers=auth_headers())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(store.reviews, [])
        # and a blocked report is never ready to sign through acknowledgments alone
        status = client.get(f"/v1/interceptor/audit/reports/{blocked['request_id']}/review-status",
                            headers=auth_headers()).json()
        self.assertFalse(status["ready_to_sign"])

    def test_the_scope_is_required(self):
        weak = auth_headers(scope="interceptor:read interceptor:override")
        r = self.client.post(ACK, json=body(self.report_id, self.span_id), headers=weak)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.store.reviews, [])

    def test_a_bad_action_is_a_validation_error(self):
        r = self.client.post(ACK, json=body(self.report_id, self.span_id, action="IGNORE"), headers=auth_headers())
        self.assertEqual(r.status_code, 400)

    def test_an_unknown_report_has_no_status(self):
        self.assertEqual(self.status("00000000-0000-4000-8000-000000000000").status_code, 404)


if __name__ == "__main__":
    unittest.main()
