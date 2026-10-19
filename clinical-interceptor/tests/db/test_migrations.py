"""Alembic migration 0001 against PostgreSQL (PLAN 5.1). Skipped unless INTERCEPTOR_TEST_DSN is set.

Drops and recreates the public schema of the DSN's database, then migrates it up, down and up again.
"""

import os
import unittest

import psycopg
from alembic import command
from alembic.config import Config

from tests.support import ROOT

DSN = os.environ.get("INTERCEPTOR_TEST_DSN")
TABLES = {"intercepted_notes", "audit_traces", "hallucination_events", "hallucination_reviews"}
PID = "pid_" + "a" * 64


def config():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def tables():
    with psycopg.connect(DSN) as conn:
        rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()
    return {r[0] for r in rows}


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = os.environ.get("INTERCEPTOR_DATABASE_URL")
        os.environ["INTERCEPTOR_DATABASE_URL"] = DSN

    @classmethod
    def tearDownClass(cls):
        if cls._saved is None:
            os.environ.pop("INTERCEPTOR_DATABASE_URL", None)
        else:
            os.environ["INTERCEPTOR_DATABASE_URL"] = cls._saved

    def reset(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")

    def test_up_down_up(self):
        self.reset()
        command.upgrade(config(), "head")
        self.assertTrue(TABLES <= tables())
        command.downgrade(config(), "base")
        self.assertFalse(TABLES & tables())
        with psycopg.connect(DSN) as conn:  # the downgrade also removed the types and the trigger function
            left = conn.execute("SELECT count(*) FROM pg_type WHERE typname IN ('pipeline_stage', 'nli_class', "
                                "'risk_severity', 'hallucination_category')").fetchone()[0]
            function = conn.execute("SELECT count(*) FROM pg_proc WHERE proname = 'forbid_audit_mutation'").fetchone()[0]
        self.assertEqual((left, function), (0, 0))
        command.upgrade(config(), "head")
        self.assertTrue(TABLES <= tables())

    def test_the_migrated_schema_is_append_only_and_constrained(self):
        self.reset()
        command.upgrade(config(), "head")
        with psycopg.connect(DSN) as conn:
            note = conn.execute(
                "INSERT INTO intercepted_notes (patient_pseudo_id, source_system, note_type, note_sha256, char_length) "
                "VALUES (%s, 's', 't', %s, 1) RETURNING note_id", (PID, "b" * 64)).fetchone()[0]
            trace = conn.execute(
                "INSERT INTO audit_traces (note_id, patient_pseudo_id, stage, component_name, component_version, "
                "input_sha256, status) VALUES (%s, %s, 'VERIFY', 'c', 'v', %s, 'OK') RETURNING trace_id",
                (note, PID, "c" * 64)).fetchone()[0]
            conn.execute(
                "INSERT INTO hallucination_events (trace_id, note_id, patient_pseudo_id, claim_id, category, "
                "risk_severity, nli_class, confidence, claim_text) VALUES (%s, %s, %s, 'clm_000000000001', 'OTHER', "
                "'WARNING', 'NEUTRAL', 0.9, 'x')", (trace, note, PID))
        with self.assertRaises(psycopg.Error):  # UPDATE on audit_traces is rejected by the trigger
            with psycopg.connect(DSN) as conn:
                conn.execute("UPDATE audit_traces SET status = 'ERROR', error_message = 'x'")
        with self.assertRaises(psycopg.Error):  # DELETE on hallucination_events is rejected
            with psycopg.connect(DSN) as conn:
                conn.execute("DELETE FROM hallucination_events")
        with self.assertRaises(psycopg.errors.CheckViolation):  # SAFE is never recorded as an event
            with psycopg.connect(DSN) as conn:
                conn.execute(
                    "INSERT INTO hallucination_events (trace_id, note_id, patient_pseudo_id, claim_id, category, "
                    "risk_severity, nli_class, confidence, claim_text) VALUES (%s, %s, %s, 'clm_000000000002', "
                    "'OTHER', 'SAFE', 'ENTAILMENT', 0.9, 'x')", (trace, note, PID))

    def test_the_url_form_of_the_database_setting_works_too(self):
        from psycopg.conninfo import conninfo_to_dict

        parts = conninfo_to_dict(DSN)
        auth = parts.get("user", "") + (f":{parts['password']}" if parts.get("password") else "")
        url = f"postgresql://{auth}@{parts['host']}:{parts['port']}/{parts['dbname']}"
        self.reset()
        os.environ["INTERCEPTOR_DATABASE_URL"] = url
        try:
            command.upgrade(config(), "head")
        finally:
            os.environ["INTERCEPTOR_DATABASE_URL"] = DSN
        self.assertTrue(TABLES <= tables())

    def test_a_missing_database_setting_is_refused(self):
        saved = os.environ.pop("INTERCEPTOR_DATABASE_URL")
        try:
            with self.assertRaises(RuntimeError):
                command.upgrade(config(), "head")
        finally:
            os.environ["INTERCEPTOR_DATABASE_URL"] = saved


if __name__ == "__main__":
    unittest.main()
