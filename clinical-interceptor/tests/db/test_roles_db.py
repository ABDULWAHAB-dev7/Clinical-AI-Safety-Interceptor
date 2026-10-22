"""db/roles.sql against PostgreSQL. Skipped unless INTERCEPTOR_TEST_DSN is set (CI runs it).

Needs a DSN whose user may create roles and own the schema. Like test_migrations, it drops and recreates the public
schema of that database, so never point it at a real one.
"""

import os
import unittest

import psycopg
from alembic import command

from tests.db.test_migrations import config
from tests.support import ROOT

DSN = os.environ.get("INTERCEPTOR_TEST_DSN")
PID_A = "pid_" + "a" * 64
PID_B = "pid_" + "b" * 64


@unittest.skipUnless(DSN, "set INTERCEPTOR_TEST_DSN to run the database tests")
class RolesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = os.environ.get("INTERCEPTOR_DATABASE_URL")
        os.environ["INTERCEPTOR_DATABASE_URL"] = DSN
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
        command.upgrade(config(), "head")
        sql = (ROOT / "db" / "roles.sql").read_text(encoding="utf-8")
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(sql)
            conn.execute(sql)  # safe to run twice
            for pid, sha in ((PID_A, "1" * 64), (PID_B, "2" * 64)):
                conn.execute(
                    "INSERT INTO intercepted_notes (patient_pseudo_id, source_system, note_type, note_sha256, char_length) "
                    "VALUES (%s, 's', 't', %s, 1)", (pid, sha))

    @classmethod
    def tearDownClass(cls):
        if cls._saved is None:
            os.environ.pop("INTERCEPTOR_DATABASE_URL", None)
        else:
            os.environ["INTERCEPTOR_DATABASE_URL"] = cls._saved

    def as_role(self, role, scope=None):
        conn = psycopg.connect(DSN, autocommit=True)
        conn.execute(f"SET ROLE {role}")
        if scope is not None:
            conn.execute("SELECT set_config('interceptor.patient_scope', %s, false)", (scope,))
        return conn

    def count(self, role, scope=None):
        with self.as_role(role, scope) as conn:
            return conn.execute("SELECT count(*) FROM intercepted_notes").fetchone()[0]

    def test_the_service_sees_every_patient(self):
        self.assertEqual(self.count("interceptor_app"), 2)

    def test_a_reader_sees_nothing_until_it_names_a_patient(self):
        for role in ("interceptor_audit", "interceptor_safety"):
            with self.subTest(role=role):
                self.assertEqual(self.count(role), 0)
                self.assertEqual(self.count(role, PID_A), 1)
                self.assertEqual(self.count(role, "*"), 2)

    def test_the_view_does_not_leak_other_patients(self):
        with self.as_role("interceptor_audit", PID_A) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM v_hallucination_current").fetchone()[0], 0)

    def test_a_reader_cannot_write(self):
        with self.as_role("interceptor_audit", "*") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("DELETE FROM intercepted_notes")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE intercepted_notes SET pipeline_status = 'VERIFIED'")

    def test_the_service_cannot_delete_or_edit_identity_columns(self):
        with self.as_role("interceptor_app") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("DELETE FROM intercepted_notes")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE intercepted_notes SET patient_pseudo_id = %s", (PID_B,))
            conn.execute("UPDATE intercepted_notes SET pipeline_status = 'VERIFIED' WHERE patient_pseudo_id = %s", (PID_A,))


if __name__ == "__main__":
    unittest.main()
