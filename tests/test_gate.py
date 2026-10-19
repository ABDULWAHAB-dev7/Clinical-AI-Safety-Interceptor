"""Run with: python -m unittest discover -s tests -v"""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from gate import (
    FLAGGED,
    QUARANTINED_UNAVAILABLE,
    READY,
    RELEASED_BY_OVERRIDE,
    RELEASED_UNVERIFIED,
    AuditLog,
    FailOpenRule,
    ReviewSession,
    override,
    run_gate,
)
from gate import policy
from gate.assertions import split_spans
from gate.model import CONTRADICTED, QUARANTINED, SUPPORTED, UNGROUNDED
from gate.verify import facts_from_record, verify_span

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
RECORD = json.loads((EXAMPLES / "record.json").read_text(encoding="utf-8"))


def load_draft(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class VerifyTests(unittest.TestCase):
    def verdict(self, text):
        return verify_span("s1", text, facts_from_record(RECORD))

    def test_negated_absent_finding_is_supported(self):
        self.assertEqual(self.verdict("No pleural effusion.").label, SUPPORTED)

    def test_affirmed_absent_finding_is_contradicted(self):
        self.assertEqual(self.verdict("Pleural effusion present on chest x-ray.").label, CONTRADICTED)

    def test_negated_recorded_medication_is_contradicted(self):
        self.assertEqual(self.verdict("Patient is not on metformin.").label, CONTRADICTED)

    def test_unrecorded_drug_is_ungrounded(self):
        self.assertEqual(self.verdict("Started on lisinopril 10 mg daily.").label, UNGROUNDED)

    def test_split_spans_numbers_sentences(self):
        self.assertEqual([sid for sid, _ in split_spans("A b. C d! E?")], ["s1", "s2", "s3"])


class JourneyTests(unittest.TestCase):
    def test_journey_a_clean_document_is_ready(self):
        result = run_gate(load_draft("draft_clean.json"), RECORD, "Facility-A")
        self.assertEqual(result["status"], READY)
        self.assertTrue(all(v["label"] == SUPPORTED for v in result["verdicts"]))

    def test_journey_b_ungrounded_is_flagged_and_must_be_acknowledged(self):
        result = run_gate(load_draft("draft_flagged.json"), RECORD, "Facility-A")
        self.assertEqual(result["status"], FLAGGED)

        session = ReviewSession(result)
        can_sign, missing = session.can_sign()
        self.assertFalse(can_sign)
        self.assertEqual(len(missing), 1)

        session.acknowledge(missing[0], "reject", "CLIN-7")
        self.assertEqual(session.can_sign(), (True, []))

    def test_journey_c_contradicted_is_quarantined_and_cannot_sign(self):
        result = run_gate(load_draft("draft_contradicted.json"), RECORD, "Facility-A")
        self.assertEqual(result["status"], QUARANTINED)
        self.assertIn("clinician", result["notify"])
        self.assertEqual(ReviewSession(result).can_sign()[0], False)

    def test_override_requires_reason_and_approver(self):
        result = run_gate(load_draft("draft_contradicted.json"), RECORD, "Facility-A")
        with self.assertRaises(ValueError):
            override(result, "  ", "APP-1")
        with self.assertRaises(ValueError):
            override(result, "Confirmed on repeat film", "")

        released = override(result, "Confirmed on repeat film", "APP-1")
        self.assertEqual(released["status"], RELEASED_BY_OVERRIDE)
        self.assertEqual(released["override"]["approver_id"], "APP-1")

    def test_override_refused_for_ready_document(self):
        result = run_gate(load_draft("draft_clean.json"), RECORD, "Facility-A")
        with self.assertRaises(ValueError):
            override(result, "reason", "APP-1")

    def test_invalid_acknowledgment_is_rejected(self):
        result = run_gate(load_draft("draft_flagged.json"), RECORD, "Facility-A")
        session = ReviewSession(result)
        with self.assertRaises(ValueError):
            session.acknowledge("s1", "accept", "CLIN-7")  # s1 is supported, not flagged
        with self.assertRaises(ValueError):
            session.acknowledge("s2", "ignore", "CLIN-7")


class FailModeTests(unittest.TestCase):
    def test_verification_failure_fails_closed(self):
        with mock.patch.object(policy, "verify_draft", side_effect=RuntimeError("timeout")):
            result = run_gate(load_draft("draft_clean.json"), RECORD, "Facility-A")
        self.assertEqual(result["status"], QUARANTINED_UNAVAILABLE)
        self.assertIn("timeout", result["error"])

    def test_fail_open_only_for_approved_facility_and_doc_type(self):
        rule = FailOpenRule("Facility-A", "soap", "COMP-2026-01")
        draft = load_draft("draft_clean.json")
        with mock.patch.object(policy, "verify_draft", side_effect=RuntimeError("timeout")):
            approved = run_gate(draft, RECORD, "Facility-A", fail_open_rules=(rule,))
            other = run_gate(draft, RECORD, "Facility-B", fail_open_rules=(rule,))
        self.assertEqual(approved["status"], RELEASED_UNVERIFIED)
        self.assertEqual(approved["fail_open_approval"], "COMP-2026-01")
        self.assertEqual(other["status"], QUARANTINED_UNAVAILABLE)


class AuditTests(unittest.TestCase):
    def test_chain_verifies_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "audit.jsonl"
            log = AuditLog(path)
            run_gate(load_draft("draft_clean.json"), RECORD, "Facility-A", audit=log)
            run_gate(load_draft("draft_flagged.json"), RECORD, "Facility-A", audit=log)
            self.assertEqual(log.verify(), (True, None))

            lines = path.read_text(encoding="utf-8").splitlines()
            lines[0] = lines[0].replace('"ready_for_review"', '"quarantined"')
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertEqual(log.verify(), (False, 0))


if __name__ == "__main__":
    unittest.main()
