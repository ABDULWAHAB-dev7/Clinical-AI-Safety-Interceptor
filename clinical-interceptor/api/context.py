"""Patient context from a request's FHIR bundle, shared by /verify and the proxy (API.md 4.1.1, 6.1.1).

The bundle must be type 'collection' with at most 500 entries. Every resource is validated against the DATABASE.md
models, and a single invalid resource rejects the whole request (partial bundles are not accepted). The bundle's one
Patient must match patient_pseudo_id. Unsupported resource types are ignored with a warning.
"""

from core.pseudo import patient_pseudo_id
from fhir.bundle import MRN_SYSTEM
from fhir.ingest import RESOURCE_FILES, ingest
from fhir.triples import triples_from_ingest

from .errors import InterceptionError

BUNDLE_ENTRY_MAX = 500  # API.md 13


def load_patient_context(raw, expected_pid, state, root="fhir_bundle", pid_param="patient_pseudo_id"):
    """Returns (triples, warnings). Raises InterceptionError with the API.md 11.2 code for every failure."""
    if not isinstance(raw, dict) or raw.get("resourceType") != "Bundle" or raw.get("type") != "collection":
        raise InterceptionError("INTERCEPT_BUNDLE_INVALID", "fhir_bundle must be a Bundle with type 'collection'.",
                                f"{root}.type")
    entries = raw.get("entry", [])
    if not isinstance(entries, list):
        raise InterceptionError("INTERCEPT_BUNDLE_INVALID", "fhir_bundle.entry must be a list.", f"{root}.entry")
    if len(entries) > BUNDLE_ENTRY_MAX:
        raise InterceptionError("INTERCEPT_BUNDLE_TOO_LARGE", "The bundle exceeds 500 entries.", f"{root}.entry")

    warnings, declared = [], []
    for index, entry in enumerate(entries):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        rtype = resource.get("resourceType") if isinstance(resource, dict) else None
        if rtype in RESOURCE_FILES:
            declared.append((rtype, f"entry[{index}]", resource))
        else:
            warnings.append({"code": "INTERCEPT_UNSUPPORTED_RESOURCE",
                             "message": "A resource type is not supported and was ignored.",
                             "param": f"{root}.entry[{index}]"})

    ingested = ingest(declared)
    if ingested.rejected:
        if any("is not a Patient in this batch" in reason for _, _, reason in ingested.rejected):
            raise InterceptionError("INTERCEPT_FHIR_REFERENCE_UNRESOLVED",
                                    "A subject or patient reference does not resolve to a Patient in the bundle.",
                                    f"{root}.entry")
        raise InterceptionError("INTERCEPT_BUNDLE_INVALID", "A resource in fhir_bundle fails validation.",
                                f"{root}.entry")
    patients = [r for (t, _), r in ingested.resources.items() if t == "Patient"]
    if len(patients) != 1:
        raise InterceptionError("INTERCEPT_BUNDLE_INVALID", "fhir_bundle must contain exactly one Patient.",
                                f"{root}.entry")
    mrn = next((i.value for i in patients[0].identifier if i.system == MRN_SYSTEM and i.value), None)
    if not mrn:
        raise InterceptionError("INTERCEPT_BUNDLE_INVALID", "The Patient in fhir_bundle has no identifier.",
                                f"{root}.entry")
    if patient_pseudo_id(mrn, state.pseudo_key) != expected_pid:
        raise InterceptionError("INTERCEPT_PATIENT_MISMATCH",
                                "The patient in fhir_bundle does not match patient_pseudo_id.", pid_param)
    for _ in ingested.unmapped:
        warnings.append({"code": "INTERCEPT_UNMAPPED_CODE",
                         "message": "A resource has a code outside SNOMED CT, LOINC and RxNorm.",
                         "param": f"{root}.entry"})
    return triples_from_ingest(ingested, state.knowledge), warnings
