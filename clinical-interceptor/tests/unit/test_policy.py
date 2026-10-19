import pathlib
import tempfile
import unittest

from interceptor.core.errors import ErrorClass, PipelineError
from interceptor.core.policy import load_policy
from tests.support import POLICY_PATH

VALID = """policy_version: "9.9.9"
doc_types_in_scope: [soap_note]
nli:
  entailment_threshold: 0.9
  contradiction_threshold: 0.9
tolerances:
  observation_lab_abs: 0.5
"""


def write_policy(text):
    tmp = tempfile.TemporaryDirectory()
    path = pathlib.Path(tmp.name) / "policy.yaml"
    path.write_text(text, encoding="utf-8")
    return tmp, path


class PolicyLoadTests(unittest.TestCase):
    def test_default_policy_loads(self):
        policy = load_policy(POLICY_PATH)
        self.assertEqual(policy.version, "0.1.0")
        self.assertIn("soap_note", policy.doc_types_in_scope)

    def test_valid_custom_policy_loads(self):
        tmp, path = write_policy(VALID)
        with tmp:
            self.assertEqual(load_policy(path).lab_tolerance_abs, 0.5)

    def _assert_refused(self, text):
        tmp, path = write_policy(text)
        with tmp, self.assertRaises(PipelineError) as ctx:
            load_policy(path)
        self.assertEqual(ctx.exception.error_class, ErrorClass.POLICY_LOAD_FAILURE)

    def test_missing_file_refused(self):
        with self.assertRaises(PipelineError) as ctx:
            load_policy("does-not-exist.yaml")
        self.assertEqual(ctx.exception.error_class, ErrorClass.POLICY_LOAD_FAILURE)

    def test_unknown_key_refused(self):
        # A policy cannot switch off hard-stop rules by adding keys (architecture section 5).
        self._assert_refused(VALID + "disable_hard_stops: true\n")

    def test_out_of_range_threshold_refused(self):
        self._assert_refused(VALID.replace("0.9", "1.5"))

    def test_negative_tolerance_refused(self):
        self._assert_refused(VALID.replace("0.5", "-1"))

    def test_malformed_yaml_refused(self):
        self._assert_refused("policy_version: [unclosed")


if __name__ == "__main__":
    unittest.main()
