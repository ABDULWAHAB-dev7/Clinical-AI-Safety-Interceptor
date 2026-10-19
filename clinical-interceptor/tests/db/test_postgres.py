"""Live PostgreSQL tests. Skipped unless INTERCEPTOR_TEST_DSN is set.

Use a dedicated, disposable database: setUpClass DROPS and recreates the public schema.

    INTERCEPTOR_TEST_DSN="host=127.0.0.1 port=55432 dbname=interceptor_test user=postgres" \
        python -m unittest discover -s tests -t .
"""

import os
import unittest

import psycopg
from psycopg import errors

from store import postgres as db

DSN = os.environ.get("INTERCEPTOR_TEST_DSN")
PID = "pid_" + "a" * 64
PID_B = "pid_" + "b" * 64
HASH = "c" * 64


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class PostgresAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        with psycopg.connect(DSN) as conn:
            db.apply_schema(conn)

    def new_note(self, conn, pid=PID, sha=HASH):
        return db.add_note(conn, patient_pseudo_id=pid, source_system="ehr-test", note_type="PROGRESS_NOTE",
                           note_sha256=sha, char_length=179)

    def new_trace(self, conn, note_id, pid=PID):
        return db.add_trace(conn, note_id=note_id, patient_pseudo_id=pid, stage="VERIFY",
                            component_name="verifier", component_version="0.1.0", input_sha256=HASH,
                            status="OK", output_payload={"nli_class": "CONTRADICTION"}, latency_ms=12)

    def test_full_write_path_and_current_review_state(self):
        with psycopg.connect(DSN) as conn:
            note = self.new_note(conn, sha="1" * 64)
            trace = self.new_trace(conn, note)
            event = db.add_event(conn, trace_id=trace, note_id=note, patient_pseudo_id=PID,
                                 claim_id="clm_000000000001", category="DRUG_ALLERGY_CONFLICT",
                                 risk_severity="CRITICAL", nli_class="CONTRADICTION", confidence=0.93,
                                 claim_text="Patient denies known drug allergies", risk_rule="contradiction_high_risk",
                                 matched_fhir_ref="AllergyIntolerance/allergy-penicillin-001",
                                 doc_char_start=48, doc_char_end=83)
            self.assertEqual(db.current_resolution(conn, event), "OPEN")
        with psycopg.connect(DSN) as conn:
            db.add_review(conn, event_id=event, resolution="CONFIRMED_ERROR", reviewer_ref="rev_" + "0" * 16)
        with psycopg.connect(DSN) as conn:
            self.assertEqual(db.current_resolution(conn, event), "CONFIRMED_ERROR")

    def test_audit_traces_reject_update_and_delete(self):
        with psycopg.connect(DSN) as conn:
            note = self.new_note(conn, sha="2" * 64)
            trace = self.new_trace(conn, note)
        with self.assertRaises(psycopg.Error) as ctx:
            with psycopg.connect(DSN) as conn:
                conn.execute("UPDATE audit_traces SET status = 'ERROR' WHERE trace_id = %s", (trace,))
        self.assertIn("append-only", str(ctx.exception))
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(DSN) as conn:
                conn.execute("DELETE FROM audit_traces WHERE trace_id = %s", (trace,))

    def test_events_and_reviews_reject_mutation(self):
        with psycopg.connect(DSN) as conn:
            note = self.new_note(conn, sha="3" * 64)
            trace = self.new_trace(conn, note)
            event = db.add_event(conn, trace_id=trace, note_id=note, patient_pseudo_id=PID,
                                 claim_id="clm_000000000009", category="UNSUPPORTED_CLAIM",
                                 risk_severity="WARNING", nli_class="NEUTRAL", confidence=0.58,
                                 claim_text="Blood pressure is stable")
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(DSN) as conn:
                conn.execute("UPDATE hallucination_events SET risk_severity = 'SAFE' WHERE event_id = %s", (event,))

    def test_malformed_patient_pseudo_id_rejected(self):
        with self.assertRaises(errors.CheckViolation):
            with psycopg.connect(DSN) as conn:
                self.new_note(conn, pid="pid_not-hex", sha="4" * 64)

    def test_safe_severity_is_not_recorded_as_event(self):
        with psycopg.connect(DSN) as conn:
            note = self.new_note(conn, sha="5" * 64)
            trace = self.new_trace(conn, note)
        with self.assertRaises(errors.CheckViolation):
            with psycopg.connect(DSN) as conn:
                db.add_event(conn, trace_id=trace, note_id=note, patient_pseudo_id=PID,
                             claim_id="clm_000000000010", category="OTHER", risk_severity="SAFE",
                             nli_class="ENTAILMENT", confidence=0.9, claim_text="HbA1c was 8.2%")

    def test_error_status_requires_message(self):
        with psycopg.connect(DSN) as conn:
            note = self.new_note(conn, sha="6" * 64)
        with self.assertRaises(errors.CheckViolation):
            with psycopg.connect(DSN) as conn:
                db.add_trace(conn, note_id=note, patient_pseudo_id=PID, stage="RETRIEVE",
                             component_name="fhir", component_version="0.1.0", input_sha256=HASH, status="ERROR")

    def test_same_note_hash_for_same_patient_is_unique(self):
        with psycopg.connect(DSN) as conn:
            self.new_note(conn, sha="7" * 64)
        with self.assertRaises(errors.UniqueViolation):
            with psycopg.connect(DSN) as conn:
                self.new_note(conn, sha="7" * 64)

    def test_same_hash_for_different_patient_is_allowed(self):
        with psycopg.connect(DSN) as conn:
            self.new_note(conn, pid=PID_B, sha="8" * 64)


if __name__ == "__main__":
    unittest.main()
