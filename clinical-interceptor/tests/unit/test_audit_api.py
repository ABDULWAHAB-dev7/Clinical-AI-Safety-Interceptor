import copy
import json
import unittest
import uuid

from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import JwtVerifier
from api.interceptor import _idempotency_scope
from core.pseudo import patient_pseudo_id
from store.records import MemoryAuditStore, StageRecord
from tests.support import FHIR_DIR, POLICY_PATH, PUBLIC_PEM, auth_headers, security

KEY = b"audit-api-test-key-0123456789"
PID = patient_pseudo_id("SYNTH-MRN-0001", KEY)
OTHER_PID = patient_pseudo_id("SYNTH-MRN-0002", KEY)
NOTE = ("Chief complaint: Follow-up for type 2 diabetes. Patient denies known drug allergies. "
        "HbA1c 8.2% on 2026-09-02. Started amoxicillin 500 mg TID for sinusitis. Blood pressure stable.")
ALLERGY_REF = "AllergyIntolerance/allergy-penicillin-001"


def result_for(claim, **override):
    text = claim["claim_text"]
    base = {"result_id": str(uuid.uuid4()), "claim_id": claim["claim_id"], "matched_fhir_ref": None,
            "nli_class": "NEUTRAL", "confidence": 0.9, "risk_severity": "WARNING", "risk_rule": "unsupported_clinical",
            "verifier_version": "test"}
    if "denies known drug allergies" in text:
        base |= {"nli_class": "CONTRADICTION", "risk_severity": "CRITICAL", "risk_rule": "contradiction_high_risk",
                 "matched_fhir_ref": ALLERGY_REF}
    elif "amoxicillin" in text:
        base |= {"risk_severity": "CRITICAL", "risk_rule": "drug_allergy_conflict", "matched_fhir_ref": ALLERGY_REF}
    elif "HbA1c" in text:
        base |= {"nli_class": "ENTAILMENT", "risk_severity": "SAFE", "risk_rule": "entailed",
                 "matched_fhir_ref": "Observation/obs-hba1c-001"}
    elif "type 2 diabetes" in text:
        base |= {"nli_class": "ENTAILMENT", "risk_severity": "SAFE", "risk_rule": "entailed",
                 "matched_fhir_ref": "Condition/cond-t2dm-001"}
    return base | override


class AuditBase(unittest.TestCase):
    """Runs against the in-memory store. tests/db/test_audit_endpoints_db.py reruns every test on PostgreSQL."""

    def make_store(self):
        return MemoryAuditStore()

    def is_memory(self):
        return isinstance(self.store, MemoryAuditStore)

    def _rows(self, sql):
        with self.store._connect() as conn:
            return [r[0] for r in conn.execute(sql).fetchall()]

    def event_count(self):
        if self.is_memory():
            return len(self.store.events)
        return self._rows("SELECT count(*) FROM hallucination_events")[0]

    def dump_events(self):
        if self.is_memory():
            return json.dumps(self.store.events, default=str)
        return " ".join(self._rows("SELECT row_to_json(e)::text FROM hallucination_events e"))

    def dump_payloads(self):
        if self.is_memory():
            return json.dumps([t["payload"] for t in self.store.traces], default=str)
        return " ".join(self._rows("SELECT output_payload::text FROM audit_traces"))

    def setUp(self):
        self.store = self.make_store()
        app = create_app(policy_path=POLICY_PATH, fhir_dir=FHIR_DIR, store=self.store, pseudo_key=KEY,
                         verifier=JwtVerifier(PUBLIC_PEM), **security())
        self.client = TestClient(app)
        self.headers = auth_headers()
        created = self.client.post(
            "/v1/interceptor/decompose",
            json={"patient_pseudo_id": PID, "note": {"source_system": "EHR", "note_type": "PROGRESS_NOTE", "text": NOTE}},
            headers={"Idempotency-Key": str(uuid.uuid4())} | self.headers).json()
        self.note_id = created["note_id"]
        self.claims = created["claims"]

    def body(self, **changes):
        body = {"note_id": self.note_id, "patient_pseudo_id": PID, "verify_trace_ids": [],
                "claims": copy.deepcopy(self.claims), "results": [result_for(c) for c in self.claims]}
        return body | changes

    def post(self, body=None, key=None, headers=None):
        key = key or str(uuid.uuid4())
        return self.client.post("/v1/interceptor/audit", json=body or self.body(),
                                headers={"Idempotency-Key": key} | (headers or self.headers))


class AuditEndpointTests(AuditBase):
    def test_report_is_created_with_events_spans_and_note_status(self):
        r = self.post()
        self.assertEqual(r.status_code, 201, r.text)
        report = r.json()
        self.assertTrue(r.headers["Location"].endswith(report["report_id"]))
        self.assertEqual(report["pipeline_status"], "BLOCKED")
        self.assertEqual(report["overall_risk_severity"], "CRITICAL")
        self.assertEqual(report["counts"], {"CRITICAL": 2, "WARNING": 1, "SAFE": 2})
        self.assertEqual(sorted(e["category"] for e in report["hallucination_events"]),
                         ["DRUG_ALLERGY_CONFLICT", "DRUG_ALLERGY_CONFLICT", "UNSUPPORTED_CLAIM"])
        self.assertEqual(len(report["attribution_spans"]), 5)
        self.assertFalse(report["idempotent_replay"])
        note = self.store.get_note(self.note_id)
        self.assertEqual((note["pipeline_status"], note["claim_count"]), ("BLOCKED", 5))

    def test_spans_are_generated_from_results(self):
        spans = self.post().json()["attribution_spans"]
        by_class = sorted(s["classification"] for s in spans)
        self.assertEqual(by_class.count("SOURCE_SUPPORTED"), 2)
        self.assertEqual(by_class.count("NO_SOURCE_EVIDENCE"), 2)  # amoxicillin is NEUTRAL, and so is the BP claim
        self.assertEqual(by_class.count("SOURCE_CONTRADICTED"), 1)
        self.assertTrue(all(s["explanation"] for s in spans))

    def test_replay_with_the_same_key_and_body_returns_the_report_and_writes_no_events(self):
        key = str(uuid.uuid4())
        body = self.body()  # the same body, including the same result IDs
        first = self.post(body, key=key).json()
        events_before = self.event_count()
        replay = self.post(body, key=key)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["report_id"], first["report_id"])
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(self.event_count(), events_before)

    def test_same_key_with_a_different_body_is_409(self):
        key = str(uuid.uuid4())
        body = self.body()
        self.post(body, key=key)
        changed = copy.deepcopy(body)
        changed["results"][0]["confidence"] = 0.5
        r = self.post(changed, key=key)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "IDEMPOTENCY_KEY_REUSED"))

    def test_idempotency_keys_are_scoped_to_the_client(self):
        key = str(uuid.uuid4())
        self.post(key=key)
        self.assertIsNotNone(self.store.find_idempotency(_idempotency_scope("client-test", key)))
        self.assertIsNone(self.store.find_idempotency(_idempotency_scope("someone-else", key)))

    def test_expired_keys_are_forgotten(self):
        if not self.is_memory():
            self.skipTest("needs a controllable clock; the PostgreSQL TTL is checked in the database tests")
        from datetime import datetime, timedelta, timezone
        now = [datetime(2026, 10, 19, tzinfo=timezone.utc)]
        self.store.clock = lambda: now[0]
        key = str(uuid.uuid4())
        body = self.body()
        self.post(body, key=key)
        now[0] += timedelta(hours=25)
        changed = copy.deepcopy(body)
        changed["results"][0]["confidence"] = 0.4  # a different body would be 409 within 24 hours
        self.assertEqual(self.post(changed, key=key).status_code, 201)

    def test_missing_idempotency_key_is_400(self):
        r = self.client.post("/v1/interceptor/audit", json=self.body(), headers=self.headers)
        self.assertEqual((r.status_code, r.json()["error"]["param"]), (400, "Idempotency-Key"))

    def test_a_missing_result_is_a_claim_mismatch(self):
        body = self.body()
        body["results"].pop()
        r = self.post(body)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "AUDIT_CLAIM_MISMATCH"))

    def test_a_result_for_an_unknown_claim_is_a_claim_mismatch(self):
        body = self.body()
        body["results"][0]["claim_id"] = "clm_ffffffffffff"
        self.assertEqual(self.post(body).json()["error"]["code"], "AUDIT_CLAIM_MISMATCH")

    def test_a_claim_whose_text_was_edited_does_not_belong_to_the_note(self):
        body = self.body()
        body["claims"][0]["claim_text"] = body["claims"][0]["claim_text"][:-1]
        body["claims"][0]["char_end"] -= 1
        self.assertEqual(self.post(body).json()["error"]["code"], "AUDIT_CLAIM_MISMATCH")

    def test_unknown_note_is_404(self):
        r = self.post(self.body(note_id=str(uuid.uuid4())))
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, "NOTE_NOT_FOUND"))

    def test_note_of_another_patient_is_422(self):
        r = self.post(self.body(patient_pseudo_id=OTHER_PID))
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "AUDIT_NOTE_PATIENT_MISMATCH"))

    def test_verify_trace_must_exist_for_this_note(self):
        r = self.post(self.body(verify_trace_ids=[str(uuid.uuid4())]))
        self.assertEqual(r.json()["error"]["code"], "AUDIT_CLAIM_MISMATCH")

    def test_a_real_verify_trace_is_accepted(self):
        stage = StageRecord("VERIFY", "verifier", "t", "0" * 64, {}, "OK")
        trace_id = self.store.record_verify(self.note_id, stage)
        self.assertEqual(self.post(self.body(verify_trace_ids=[trace_id])).status_code, 201)

    def test_only_selected_severities_create_events(self):
        body = self.body(options={"create_events_for": ["CRITICAL"]})
        events = self.post(body).json()["hallucination_events"]
        self.assertEqual({e["risk_severity"] for e in events}, {"CRITICAL"})

    def test_all_safe_results_verify_the_note_and_create_no_events(self):
        results = [result_for(c, nli_class="ENTAILMENT", risk_severity="SAFE", risk_rule="entailed",
                              matched_fhir_ref="Condition/cond-t2dm-001") for c in self.claims]
        report = self.post(self.body(results=results)).json()
        self.assertEqual((report["pipeline_status"], report["hallucination_events"]), ("VERIFIED", []))

    def test_client_explanations_are_not_stored(self):
        claim = self.claims[0]
        res = result_for(claim)
        span = {"span_id": str(uuid.uuid4()), "note_id": self.note_id, "doc_char_start": claim["char_start"],
                "doc_char_end": claim["char_end"], "classification": "SOURCE_CONTRADICTED",
                "claim_id": claim["claim_id"], "result_id": res["result_id"],
                "explanation": "CLIENT-SUPPLIED-TEXT about penicillin"}
        body = self.body(results=[res] + [result_for(c) for c in self.claims[1:]], attribution=[span])
        report = self.post(body).json()
        self.assertNotIn("CLIENT-SUPPLIED-TEXT", json.dumps(report))
        self.assertNotIn("CLIENT-SUPPLIED-TEXT", self.dump_payloads())
        self.assertEqual(len(report["attribution_spans"]), 1)

    def test_attribution_for_another_note_is_rejected(self):
        claim = self.claims[0]
        span = {"span_id": str(uuid.uuid4()), "note_id": str(uuid.uuid4()), "doc_char_start": 0, "doc_char_end": 5,
                "classification": "NON_CLINICAL", "explanation": "x"}
        self.assertEqual(self.post(self.body(attribution=[span])).json()["error"]["code"], "AUDIT_CLAIM_MISMATCH")

    def test_stored_events_hold_no_draft_text(self):
        self.post()
        stored = self.dump_events() + self.dump_payloads()
        for fragment in ("denies", "amoxicillin", "sinusitis", "Blood pressure"):
            self.assertNotIn(fragment, stored)

    def test_inconsistent_result_is_400(self):
        body = self.body()
        body["results"][0].update(nli_class="ENTAILMENT", matched_fhir_ref=None, risk_severity="SAFE",
                                  risk_rule="entailed")
        self.assertEqual(self.post(body).status_code, 400)

    def test_audit_store_failure_is_503_and_nothing_is_written(self):
        if not self.is_memory():
            self.skipTest("failure injection is memory-only; the database rollback is tested separately")
        self.store.fail = True
        r = self.post()
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (503, "AUDIT_WRITE_FAILED"))
        self.assertEqual(self.event_count(), 0)

    def test_requires_the_audit_write_scope(self):
        r = self.post(headers=auth_headers("interceptor:read"))
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "AUTH_SCOPE_INSUFFICIENT"))


class ReadEndpointTests(AuditBase):
    def test_report_can_be_read_back_with_the_same_shape(self):
        created = self.post().json()
        r = self.client.get(f"/v1/interceptor/audit/reports/{created['report_id']}", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        read = r.json()
        self.assertEqual(read.keys(), created.keys())
        self.assertEqual(read["hallucination_events"], created["hallucination_events"])
        self.assertEqual(read["attribution_spans"][0]["explanation"], created["attribution_spans"][0]["explanation"])

    def test_unknown_or_malformed_report_id_is_404(self):
        for report_id in (str(uuid.uuid4()), "not-a-uuid"):
            r = self.client.get(f"/v1/interceptor/audit/reports/{report_id}", headers=self.headers)
            self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, "REPORT_NOT_FOUND"))

    def test_note_read_returns_metadata_and_a_claim_index_without_text(self):
        r = self.client.get(f"/v1/interceptor/notes/{self.note_id}", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        note = r.json()
        self.assertEqual((note["note_id"], note["patient_pseudo_id"], note["char_length"]),
                         (self.note_id, PID, len(NOTE)))
        self.assertEqual(note["claim_count"], len(self.claims))
        self.assertEqual([c["claim_id"] for c in note["claims"]], [c["claim_id"] for c in self.claims])
        self.assertNotIn("claim_text", note["claims"][0])
        self.assertNotIn("denies", json.dumps(note))

    def test_note_status_reflects_the_audit(self):
        self.post()
        note = self.client.get(f"/v1/interceptor/notes/{self.note_id}", headers=self.headers).json()
        self.assertEqual(note["pipeline_status"], "BLOCKED")

    def test_unknown_note_is_404(self):
        r = self.client.get(f"/v1/interceptor/notes/{uuid.uuid4()}", headers=self.headers)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, "NOTE_NOT_FOUND"))

    def test_reads_need_the_read_scope(self):
        r = self.client.get(f"/v1/interceptor/notes/{self.note_id}", headers=auth_headers("interceptor:verify"))
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
