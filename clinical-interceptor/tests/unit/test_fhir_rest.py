"""The FHIR REST source, tested offline with a mock transport. A live check is in tests/integration."""

import unittest

import httpx

from interceptor.fhir.rest import FhirFetchError, check_base, fetch_everything

BASE = "https://fhir.example.org/r4"
PATIENT = {"resourceType": "Patient", "id": "p1", "identifier": [{"system": "urn:synthetic:mrn", "value": "M1"}]}


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def everything(entries, links=()):
    return {"resourceType": "Bundle", "type": "searchset", "entry": entries, "link": list(links)}


class FetchTests(unittest.TestCase):
    def test_keeps_supported_resources_and_reports_the_rest(self):
        body = everything([{"resource": PATIENT},
                           {"resource": {"resourceType": "Condition", "id": "c1"}},
                           {"resource": {"resourceType": "Immunization", "id": "i1"}},
                           {"resource": {"resourceType": "Immunization", "id": "i2"}}])

        def handler(request):
            self.assertEqual(request.url.path, "/r4/Patient/p1/$everything")
            return httpx.Response(200, json=body)

        bundle, warnings = fetch_everything(BASE, "p1", client=client_for(handler))
        self.assertEqual(bundle["type"], "collection")
        self.assertEqual([e["resource"]["resourceType"] for e in bundle["entry"]], ["Patient", "Condition"])
        self.assertEqual([w["code"] for w in warnings], ["FHIR_RESOURCE_NOT_SUPPORTED"])
        self.assertIn("2 Immunization", warnings[0]["message"])

    def test_a_next_link_is_reported_as_paging(self):
        body = everything([{"resource": PATIENT}], links=[{"relation": "next", "url": "https://x"}])
        bundle, warnings = fetch_everything(BASE, "p1", client=client_for(lambda r: httpx.Response(200, json=body)))
        self.assertIn("FHIR_PAGED", [w["code"] for w in warnings])

    def test_server_errors_and_non_bundles_fail(self):
        with self.assertRaises(FhirFetchError) as ctx:
            fetch_everything(BASE, "p1", client=client_for(lambda r: httpx.Response(404)))
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(FhirFetchError):
            fetch_everything(BASE, "p1", client=client_for(lambda r: httpx.Response(200, json={"resourceType": "X"})))
        with self.assertRaises(FhirFetchError):
            fetch_everything(BASE, "p1", client=client_for(lambda r: httpx.Response(200, text="not json")))

    def test_network_errors_are_fetch_errors(self):
        def boom(request):
            raise httpx.ConnectError("down")

        with self.assertRaises(FhirFetchError) as ctx:
            fetch_everything(BASE, "p1", client=client_for(boom))
        self.assertEqual(ctx.exception.status, 0)

    def test_plain_http_is_refused_except_for_localhost(self):
        with self.assertRaises(ValueError):
            check_base("http://fhir.example.org/r4")
        self.assertEqual(check_base("http://localhost:8080/fhir"), "http://localhost:8080/fhir")
        self.assertEqual(check_base("https://fhir.example.org/r4/"), "https://fhir.example.org/r4")

    def test_a_patient_id_with_path_characters_is_refused(self):
        for bad in ("../Observation", "p1/x", "p 1", ""):
            with self.assertRaises(ValueError):
                fetch_everything(BASE, bad, client=client_for(lambda r: httpx.Response(200)))


if __name__ == "__main__":
    unittest.main()
