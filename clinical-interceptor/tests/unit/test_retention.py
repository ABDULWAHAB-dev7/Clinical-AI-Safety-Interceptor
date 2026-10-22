"""scripts/retention.py: the cutoff arithmetic and the guarantees that it reads only (no database needed)."""

import importlib.util
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests.support import ROOT

spec = importlib.util.spec_from_file_location("retention", Path(ROOT) / "scripts" / "retention.py")
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)

NOW = datetime(2026, 10, 22, tzinfo=UTC)


class RetentionTests(unittest.TestCase):
    def test_the_default_period_is_six_years(self):
        self.assertEqual(retention.days_from({}, None), 6 * 365)
        self.assertEqual((NOW - retention.cutoff(NOW, retention.DEFAULT_DAYS)).days, 2190)

    def test_the_environment_and_the_flag_override_the_default_in_that_order(self):
        self.assertEqual(retention.days_from({"INTERCEPTOR_RETENTION_DAYS": "30"}, None), 30)
        self.assertEqual(retention.days_from({"INTERCEPTOR_RETENTION_DAYS": "30"}, 90), 90)

    def test_a_period_under_one_day_is_refused(self):
        with self.assertRaises(ValueError):
            retention.cutoff(NOW, 0)

    def test_a_missing_database_setting_stops_before_connecting(self):
        self.assertEqual(retention.main([], environ={}, now=NOW), 2)

    def test_the_script_has_no_delete_or_update_statement(self):
        source = (Path(ROOT) / "scripts" / "retention.py").read_text(encoding="utf-8").upper()
        for word in ("DELETE FROM", "UPDATE ", "TRUNCATE", "DROP "):
            self.assertNotIn(word, source.split('"""', 2)[2], word)


if __name__ == "__main__":
    unittest.main()
