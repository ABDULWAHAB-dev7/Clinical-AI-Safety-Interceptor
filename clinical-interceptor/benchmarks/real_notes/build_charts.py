"""Builds the FHIR R4 chart (bundle.json) for each case in benchmarks/real_notes/cases/.

    python benchmarks/real_notes/build_charts.py

The charts are written before the notes and are not derived from them. A seeded error in a note is a statement that
disagrees with its chart. Every chart is synthetic; no patient is real.

Chart A: type 2 diabetes (active), penicillin allergy (active), HbA1c 8.2 % on 2026-09-02.
Chart B: penicillin allergy (active) only. No diagnosis and no lab in the record.
Chart C: type 2 diabetes (active), penicillin allergy (active), HbA1c 7.4 % on 2026-08-12.
"""

import json
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parent / "cases"

CHART_SPECS = {
    "A": {"hba1c": (8.2, "2026-09-02T08:30:00Z"), "diabetes": True, "allergy": True},
    "B": {"hba1c": None, "diabetes": False, "allergy": True},
    "C": {"hba1c": (7.4, "2026-08-12T09:10:00Z"), "diabetes": True, "allergy": True},
}

# case number -> chart letter. Cases 01 to 03 are clean; the others carry seeded errors (see gold.json).
CASE_CHART = {1: "A", 2: "A", 3: "C", 4: "A", 5: "A", 6: "B", 7: "A", 8: "A", 9: "A", 10: "C"}

CONFIRMED = {"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}


def chart(number, letter):
    spec = CHART_SPECS[letter]
    pid = f"pat-rw{number:02d}"
    subject = {"reference": f"Patient/{pid}"}
    resources = [
        {"resourceType": "Patient", "id": pid, "active": True, "gender": "male" if number % 2 else "female",
         "birthDate": "1964-07-02",
         "identifier": [{"system": "urn:synthetic:mrn", "value": f"SYNTH-MRN-RW{number:02d}", "use": "usual"}]},
        {"resourceType": "Encounter", "id": f"enc-rw{number:02d}", "status": "finished",
         "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
         "subject": subject, "period": {"start": "2026-09-02T08:00:00Z", "end": "2026-09-02T08:45:00Z"}},
    ]
    if spec["diabetes"]:
        resources.append({
            "resourceType": "Condition", "id": f"cond-rw{number:02d}-t2dm",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                                           "code": "active"}]},
            "verificationStatus": {"coding": [CONFIRMED]},
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "44054006",
                                 "display": "Diabetes mellitus type 2"}]},
            "subject": subject, "onsetDateTime": "2019-05-02T00:00:00Z"})
    if spec["allergy"]:
        resources.append({
            "resourceType": "AllergyIntolerance", "id": f"allergy-rw{number:02d}-penicillin",
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                                           "code": "active"}]},
            "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                                               "code": "confirmed"}]},
            "type": "allergy", "category": ["medication"], "criticality": "high",
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "91936005",
                                 "display": "Allergy to penicillin"}]},
            "patient": subject,
            "reaction": [{"manifestation": [{"coding": [{"system": "http://snomed.info/sct", "code": "271807003",
                                                         "display": "Skin rash"}]}]}]})
    if spec["hba1c"] is not None:
        value, when = spec["hba1c"]
        resources.append({
            "resourceType": "Observation", "id": f"obs-rw{number:02d}-hba1c", "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                      "code": "laboratory"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4",
                                 "display": "Hemoglobin A1c/Hemoglobin.total in Blood"}]},
            "subject": subject, "encounter": {"reference": f"Encounter/enc-rw{number:02d}"},
            "effectiveDateTime": when,
            "valueQuantity": {"value": value, "unit": "%", "system": "http://unitsofmeasure.org", "code": "%"}})
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in resources]}


def main():
    for number, letter in CASE_CHART.items():
        folder = CASES_DIR / f"case-{number:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "bundle.json").write_text(json.dumps(chart(number, letter), indent=2) + "\n", encoding="utf-8")
        print(f"case-{number:02d} chart {letter}")


if __name__ == "__main__":
    main()
