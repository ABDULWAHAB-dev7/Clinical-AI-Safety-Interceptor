"""FHIR R4 REST source (Phase 3): GET {base}/Patient/{id}/$everything, as a collection bundle for the verifier.

The inline bundle in a request stays the default path. This module is for a real FHIR server, such as the public HAPI
R4 server or the SMART Health IT sandbox, when a deployment has no hospital credentials. It only reads.

Rules:
  * https is required, except for localhost, so a patient's record is not sent or received in clear text.
  * Only resource types the verifier understands are kept (fhir.ingest.RESOURCE_FILES). Everything else is dropped and
    reported as a warning, never silently.
  * Only the first page is read. A "next" link is reported as a warning.
  * The result is a `collection` bundle. The verifier still validates every resource and fails closed on any invalid one.
  * Patient identity still comes from the facility's MRN identifier. A server patient without one is refused by the
    verifier, which is the intended fail-closed behaviour.
"""

import re
from urllib.parse import urlparse

import httpx

from .ingest import RESOURCE_FILES

PATIENT_ID = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
MAX_ENTRIES = 500  # the verifier accepts at most 500 bundle entries (API.md 13)


class FhirFetchError(Exception):
    def __init__(self, status, reason):
        super().__init__(f"FHIR fetch failed ({status}): {reason}")
        self.status = status
        self.reason = reason


def check_base(base_url):
    parsed = urlparse(base_url)
    if parsed.scheme == "https" and parsed.hostname:
        return base_url.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS:
        return base_url.rstrip("/")
    raise ValueError("the FHIR base URL must be https (http is allowed only for localhost)")


def fetch_everything(base_url, patient_id, *, client=None, timeout=15.0):
    """Returns (bundle, warnings). The bundle holds only resource types the verifier supports."""
    base = check_base(base_url)
    if not PATIENT_ID.match(patient_id):
        raise ValueError("patient id has characters a FHIR logical id cannot have")

    own_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=False)
    try:
        response = client.get(f"{base}/Patient/{patient_id}/$everything", headers={"Accept": "application/fhir+json"})
    except httpx.HTTPError as exc:
        raise FhirFetchError(0, type(exc).__name__)
    finally:
        if own_client:
            client.close()

    if response.status_code != 200:
        raise FhirFetchError(response.status_code, "the server did not return the record")
    try:
        body = response.json()
    except ValueError:
        raise FhirFetchError(200, "the response is not JSON")
    if not isinstance(body, dict) or body.get("resourceType") != "Bundle":
        raise FhirFetchError(200, "the response is not a FHIR Bundle")

    kept, dropped, warnings = [], {}, []
    for entry in body.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        rtype = resource.get("resourceType") if isinstance(resource, dict) else None
        if rtype in RESOURCE_FILES:
            if len(kept) < MAX_ENTRIES:
                kept.append({"resource": resource})
        else:
            dropped[str(rtype)] = dropped.get(str(rtype), 0) + 1
    for rtype, count in sorted(dropped.items()):
        warnings.append({"code": "FHIR_RESOURCE_NOT_SUPPORTED",
                         "message": f"{count} {rtype} resource(s) were not supported and were dropped.",
                         "param": None})
    if len(kept) >= MAX_ENTRIES:
        warnings.append({"code": "FHIR_TRUNCATED", "message": f"Only the first {MAX_ENTRIES} entries were kept.",
                         "param": None})
    if any(link.get("relation") == "next" for link in body.get("link", []) if isinstance(link, dict)):
        warnings.append({"code": "FHIR_PAGED", "message": "The server returned more than one page; only the first was read.",
                         "param": None})
    return {"resourceType": "Bundle", "type": "collection", "entry": kept}, warnings
