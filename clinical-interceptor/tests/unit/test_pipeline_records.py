import re
import unittest

from interceptor.core.pipeline import verify_document
from interceptor.core.policy import load_policy
from interceptor.store.records import MemoryAuditStore
from tests.support import FHIR_DIR, POLICY_PATH, load_example

KEY = b"records-test-key-0123456789ab"
POLICY = load_policy(POLICY_PATH)
CLAIM_ID = re.compile(r"^clm_[0-9a-f]{12}$")


def run(example, store, encounter="ENC-SYN-1"):
    doc = load_example(example)
    return verify_document(doc["document_text"], doc["doc_type"], encounter, policy=POLICY, fhir_dir=FHIR_DIR,
                           store=store, pseudo_key=KEY)


class RequestRecordTests(unittest.TestCase):
    def test_clean_document_writes_six_ok_stages_and_no_events(self):
        store = MemoryAuditStore()
        result = run("verify_clean.json", store)
        self.assertEqual(result.pipeline_status, "VERIFIED")
        req = store.requests[0]
        self.assertEqual([s.stage for s in req.stages],
                         ["INGEST", "RETRIEVE", "EXTRACT", "VERIFY", "ATTRIBUTE", "INTERCEPT"])
        self.assertTrue(all(s.status == "OK" for s in req.stages))
        self.assertEqual(req.events, ())
        self.assertEqual(req.claim_count, 3)

    def test_contradicted_document_writes_critical_and_warning_events(self):
        store = MemoryAuditStore()
        result = run("verify_contradicted.json", store)
        self.assertEqual(result.pipeline_status, "BLOCKED")
        req = store.requests[0]
        by_category = {e.category: e for e in req.events}
        self.assertEqual(set(by_category), {"MEDICATION_DOSE_MISMATCH", "LAB_VALUE_MISMATCH"})

        med = by_category["MEDICATION_DOSE_MISMATCH"]
        self.assertEqual((med.risk_severity, med.risk_rule), ("CRITICAL", "contradiction_high_risk"))
        self.assertEqual(med.matched_fhir_ref, "MedicationRequest/mr-1")
        self.assertEqual(med.nli_class, "CONTRADICTION")
        self.assertEqual(med.confidence, 1.0)

        lab = by_category["LAB_VALUE_MISMATCH"]
        self.assertEqual((lab.risk_severity, lab.risk_rule), ("WARNING", "contradiction"))
        self.assertEqual(lab.matched_fhir_ref, "Observation/obs-1")

    def test_allergy_error_is_not_recorded_as_a_hallucination_event(self):
        # "Allergies: none." has no coded match, so it becomes VERIFICATION_ERROR; that is a block, not a claim event.
        store = MemoryAuditStore()
        run("verify_contradicted.json", store)
        verify_stage = next(s for s in store.requests[0].stages if s.stage == "VERIFY")
        self.assertEqual(verify_stage.output_payload["verdicts"]["VERIFICATION_ERROR"], 1)
        self.assertNotIn("DRUG_ALLERGY_CONFLICT", {e.category for e in store.requests[0].events})

    def test_event_spans_point_into_the_document(self):
        store = MemoryAuditStore()
        doc = load_example("verify_contradicted.json")["document_text"]
        run("verify_contradicted.json", store)
        for e in store.requests[0].events:
            self.assertTrue(0 <= e.doc_char_start < e.doc_char_end <= len(doc))
            self.assertRegex(e.claim_id, CLAIM_ID)

    def test_claim_text_is_a_token_not_draft_text(self):
        store = MemoryAuditStore()
        run("verify_contradicted.json", store)
        for e in store.requests[0].events:
            self.assertRegex(e.claim_text, r"^claim:[a-z_]+:(affirmed|negated)$")

    def test_failure_at_retrieve_skips_later_stages(self):
        store = MemoryAuditStore()
        result = run("verify_clean.json", store, encounter="ENC-NOPE")
        self.assertEqual(result.outcome.value, "HARD_BLOCK")
        self.assertEqual(store.requests, [])  # no patient could be resolved, so nothing is recorded
        self.assertIn("RETRIEVAL_UNAVAILABLE", result.errors)


if __name__ == "__main__":
    unittest.main()
