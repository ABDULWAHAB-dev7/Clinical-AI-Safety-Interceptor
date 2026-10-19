"""A coded FHIR bundle for API tests, shaped like the examples in API.md (coded SNOMED CT and LOINC resources)."""

MRN = "SYNTH-MRN-0001"

BUNDLE = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [
        {"resource": {"resourceType": "Patient", "id": "pat-0001",
                      "identifier": [{"system": "urn:synthetic:mrn", "value": MRN}]}},
        {"resource": {
            "resourceType": "AllergyIntolerance", "id": "allergy-penicillin-001",
            "verificationStatus": {"coding": [{"code": "confirmed"}]},
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "91936005",
                                 "display": "Allergy to penicillin"}]},
            "patient": {"reference": "Patient/pat-0001"}}},
        {"resource": {
            "resourceType": "Condition", "id": "cond-t2dm-001",
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "44054006",
                                 "display": "Diabetes mellitus type 2"}]},
            "subject": {"reference": "Patient/pat-0001"}}},
        {"resource": {
            "resourceType": "Observation", "id": "obs-hba1c-001", "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4",
                                 "display": "Hemoglobin A1c/Hemoglobin.total in Blood"}]},
            "subject": {"reference": "Patient/pat-0001"},
            "effectiveDateTime": "2026-09-02T08:30:00Z",
            "valueQuantity": {"value": 8.2, "unit": "%"}}},
    ],
}
