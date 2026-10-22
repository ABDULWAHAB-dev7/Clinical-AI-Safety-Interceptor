"""The shipped example requests do what the docs say they do (PLAN: documented examples must be executable)."""

import unittest

from tests.support import auth_headers, load_example
from tests.unit.test_document_check import app_client

EXPECTED = {
    "verify_clean.json": ("PASS_THROUGH", True),
    "verify_contradicted.json": ("HARD_BLOCK", False),
}


class ExampleRequestTests(unittest.TestCase):
    def test_each_example_gives_its_documented_outcome(self):
        client, _ = app_client()
        for name, (outcome, delivered) in EXPECTED.items():
            with self.subTest(example=name):
                response = client.post("/v1/verify", json=load_example(name), headers=auth_headers())
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual((body["outcome"], body["delivered"]), (outcome, delivered))

    def test_every_example_file_is_covered(self):
        from tests.support import EXAMPLES

        shipped = {p.name for p in EXAMPLES.glob("verify_*.json")}
        self.assertEqual(shipped, set(EXPECTED), "add the new example to EXPECTED with its documented outcome")


if __name__ == "__main__":
    unittest.main()
