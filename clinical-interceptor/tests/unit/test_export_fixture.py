import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_fixture.py"


def load_script():
    spec = importlib.util.spec_from_file_location("export_fixture", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExportFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_script().export("case-0001")

    def test_fixture_comes_from_the_real_pipeline_for_case_0001(self):
        report = self.fixture["report"]
        self.assertEqual(report["pipeline_status"], "BLOCKED")
        self.assertEqual(report["counts"], {"CRITICAL": 2, "WARNING": 1, "SAFE": 2})
        self.assertEqual(len(report["hallucination_events"]), 3)
        self.assertEqual(len(report["attribution_spans"]), 5)

    def test_patient_id_is_cut_to_eight_characters(self):
        self.assertRegex(self.fixture["report"]["patient_pseudo_id"], r"^[0-9a-f]{8}$")

    def test_spans_point_into_the_note_text(self):
        text = self.fixture["note_text"]
        for span in self.fixture["report"]["attribution_spans"]:
            self.assertTrue(0 <= span["doc_char_start"] < span["doc_char_end"] <= len(text))

    def test_unknown_case_is_refused(self):
        with self.assertRaises(SystemExit):
            load_script().export("case-9999")


if __name__ == "__main__":
    unittest.main()
