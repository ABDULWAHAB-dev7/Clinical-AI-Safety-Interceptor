import copy
import unittest

from fhir.ingest import ingest, load_ndjson
from fhir.models import Observation, Patient
from tests.support import ROOT

SEED_FHIR = ROOT / "seed" / "fhir"

PATIENT = {"resourceType": "Patient", "id": "pat-1", "gender": "female"}
OBS = {
    "resourceType": "Observation", "id": "obs-1", "status": "final",
    "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4"}]},
    "subject": {"reference": "Patient/pat-1"},
    "valueQuantity": {"value": 8.2, "unit": "%"},
}


def raw(declared, data):
    return (declared, data.get("id", "?"), copy.deepcopy(data))


class IngestRuleTests(unittest.TestCase):
    def test_seed_ingests_with_no_rejections(self):
        result = ingest(load_ndjson(SEED_FHIR))
        self.assertEqual(result.rejected, [])
        self.assertEqual(len(result.resources), 5)

    def test_R1_unresolved_subject_is_rejected(self):
        result = ingest([raw("Patient", PATIENT), raw("Observation", {**OBS, "subject": {"reference": "Patient/ghost"}})])
        self.assertIn(("Observation", "obs-1"), [(t, i) for t, i, _ in result.rejected])
        self.assertNotIn(("Observation", "obs-1"), result.resources)

    def test_R3_entered_in_error_is_stored_but_excluded(self):
        result = ingest([raw("Patient", PATIENT), raw("Observation", {**OBS, "status": "entered-in-error"})])
        self.assertIn(("Observation", "obs-1"), result.resources)
        self.assertIn(("Observation", "obs-1"), result.excluded_from_context)

    def test_R2_unmapped_code_is_flagged(self):
        odd = {**OBS, "code": {"coding": [{"system": "urn:local", "code": "X1"}]}}
        result = ingest([raw("Patient", PATIENT), raw("Observation", odd)])
        self.assertIn(("Observation", "obs-1"), result.unmapped)

    def test_R4_naive_datetime_is_rejected(self):
        naive = {**OBS, "effectiveDateTime": "2026-09-02T08:30:00"}
        result = ingest([raw("Patient", PATIENT), raw("Observation", naive)])
        self.assertIn("Observation", [t for t, _, _ in result.rejected])

    def test_patient_pseudo_id_must_not_come_from_fhir(self):
        result = ingest([raw("Patient", {**PATIENT, "patient_pseudo_id": "pid_" + "a" * 64})])
        self.assertEqual(result.rejected[0][2], "patient_pseudo_id must not come from FHIR")

    def test_resource_type_must_match_file(self):
        result = ingest([raw("Observation", PATIENT)])
        self.assertEqual(len(result.rejected), 1)

    def test_observation_with_two_values_fails_validation(self):
        bad = {**OBS, "valueString": "high"}
        with self.assertRaises(Exception):
            Observation.model_validate(bad)

    def test_patient_model_ignores_unknown_fields(self):
        Patient.model_validate({**PATIENT, "someVendorField": {"x": 1}})


if __name__ == "__main__":
    unittest.main()
