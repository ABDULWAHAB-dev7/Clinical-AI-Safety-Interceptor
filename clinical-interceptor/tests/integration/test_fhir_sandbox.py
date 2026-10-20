"""Live check against a public FHIR R4 server. Skipped unless INTERCEPTOR_LIVE_FHIR=1 (it needs the internet).

    INTERCEPTOR_LIVE_FHIR=1 python -m unittest tests.integration.test_fhir_sandbox
"""

import os
import unittest

import httpx

from interceptor.fhir.rest import fetch_everything

BASE = os.environ.get("INTERCEPTOR_FHIR_BASE", "https://hapi.fhir.org/baseR4")


@unittest.skipUnless(os.environ.get("INTERCEPTOR_LIVE_FHIR") == "1", "set INTERCEPTOR_LIVE_FHIR=1 to run")
class LiveFhirTests(unittest.TestCase):
    def test_fetches_a_patient_record_with_only_supported_types(self):
        with httpx.Client(timeout=30) as client:
            found = client.get(f"{BASE}/Patient", params={"_count": 1, "_format": "json"}).json()
        patient_id = found["entry"][0]["resource"]["id"]
        bundle, warnings = fetch_everything(BASE, patient_id)
        self.assertEqual(bundle["type"], "collection")
        types = {e["resource"]["resourceType"] for e in bundle["entry"]}
        self.assertTrue(types <= {"Patient", "AllergyIntolerance", "Condition", "Encounter", "Observation"})
        self.assertIn("Patient", types)


if __name__ == "__main__":
    unittest.main()
