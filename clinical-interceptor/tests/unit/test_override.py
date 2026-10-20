"""POST /v1/interceptor/override: a clinician's review of one flagged span (2026-10-20 override record)."""

import unittest
import uuid

from tests.support import auth_headers, make_token
from tests.unit.test_document_check import CLEAN, CONTRADICTED, NEEDS_NLI, _Neutral, app_client

OVERRIDE = "/v1/interceptor/override"


def body(report_id, span_id, **changes):
    payload = {"report_id": report_id, "span_id": span_id, "override_reason": "Patient confirmed allergy resolved",
               "attending_signature": "Dr. Example Attending"}
    return payload | changes


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.client, self.store = app_client()
        self.blocked = self.client.post("/v1/verify", json=CONTRADICTED, headers=auth_headers()).json()
        self.report_id = self.blocked["request_id"]
        self.note_id = self.blocked["note_id"]
        # A blocked document returns no spans, so the clinician reads them from the report (offsets only, no text).
        report = self.client.get(f"/v1/interceptor/audit/reports/{self.report_id}", headers=auth_headers()).json()
        self.flagged = report["attribution_spans"]
        self.assertEqual(self.blocked["pipeline_status"], "BLOCKED")

    def post(self, payload, headers=None):
        return self.client.post(OVERRIDE, json=payload, headers=headers or auth_headers())

    def flagged_span(self, severity="CRITICAL"):
        """A span whose claim has a finding of the given severity."""
        event_claims = {e["claim_id"]: e["risk_severity"] for e in self.store.events}
        for span in self.flagged:
            if event_claims.get(span.get("claim_id")) == severity:
                return span["span_id"]
        self.fail(f"no {severity} span in the report")

    def test_overriding_a_blocking_finding_records_a_review_and_changes_the_note_status(self):
        r = self.post(body(self.report_id, self.flagged_span()))
        self.assertEqual(r.status_code, 201, r.text)
        out = r.json()
        self.assertEqual(out["pipeline_status"], "OVERRIDDEN_BY_CLINICIAN")
        self.assertEqual(self.store.notes[next(iter(self.store.notes))]["pipeline_status"], "OVERRIDDEN_BY_CLINICIAN")
        self.assertEqual(len(self.store.reviews), 1)
        review = self.store.reviews[0]
        self.assertEqual(review["resolution"], "FALSE_POSITIVE")
        self.assertTrue(review["reviewer_ref"].startswith("rev_") and len(review["reviewer_ref"]) == 20)

    def test_the_reason_and_signature_are_not_stored_as_text(self):
        self.post(body(self.report_id, self.flagged_span()))
        stored = repr(self.store.reviews)
        self.assertNotIn("Patient confirmed allergy resolved", stored)
        self.assertNotIn("Dr. Example Attending", stored)
        self.assertIn("reason_sha256=", self.store.reviews[0]["comment"])

    def test_the_same_finding_cannot_be_overridden_twice(self):
        span = self.flagged_span()
        self.assertEqual(self.post(body(self.report_id, span)).status_code, 201)
        r = self.post(body(self.report_id, span))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "OVERRIDE_NOT_ALLOWED")
        self.assertEqual(len(self.store.reviews), 1)

    def test_a_span_with_no_finding_cannot_be_overridden(self):
        # The clean document is released with only SAFE spans, so none of them has a finding to override.
        client, store = app_client()
        clean = client.post("/v1/verify", json=CLEAN, headers=auth_headers()).json()
        report = client.get(f"/v1/interceptor/audit/reports/{clean['request_id']}", headers=auth_headers()).json()
        r = client.post(OVERRIDE, json=body(clean["request_id"], report["attribution_spans"][0]["span_id"]),
                        headers=auth_headers())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(store.reviews, [])

    def test_a_span_that_is_not_in_the_report_is_refused(self):
        r = self.post(body(self.report_id, str(uuid.uuid4())))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "OVERRIDE_NOT_ALLOWED")

    def test_an_unknown_report_is_not_found(self):
        r = self.post(body(str(uuid.uuid4()), self.flagged_span()))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "REPORT_NOT_FOUND")

    def test_a_token_without_the_override_scope_is_refused(self):
        headers = {"Authorization": f"Bearer {make_token('interceptor:read interceptor:audit:write')}"}
        headers |= {k: v for k, v in auth_headers().items() if k != "Authorization"}
        r = self.post(body(self.report_id, self.flagged_span()), headers=headers)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "AUTH_SCOPE_INSUFFICIENT")

    def test_a_warning_only_note_keeps_its_status(self):
        client, store = app_client(nli_model=_Neutral())
        warned = client.post("/v1/verify", json=CLEAN | {"document_text": NEEDS_NLI}, headers=auth_headers()).json()
        self.assertEqual(warned["pipeline_status"], "VERIFIED")
        span = client.get(f"/v1/interceptor/audit/reports/{warned['request_id']}",
                          headers=auth_headers()).json()["attribution_spans"][0]["span_id"]
        r = client.post(OVERRIDE, json=body(warned["request_id"], span), headers=auth_headers())
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["pipeline_status"], "VERIFIED")
        self.assertEqual(len(store.reviews), 1)

    def test_an_invalid_body_is_rejected(self):
        r = self.post(body(self.report_id, self.flagged_span(), override_reason="x"))
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
