"""Initial audit schema (DATABASE.md section 3).

The DDL is db/schema.sql, applied unchanged, so there is one copy of it. Do not hand-edit it here to weaken a
constraint: any change to DATABASE.md needs approval first (PLAN 5.1, Gate G3), and then goes in a new revision.

Revision ID: 0001
Revises:
"""

import pathlib

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = pathlib.Path(__file__).resolve().parents[2] / "db" / "schema.sql"


def upgrade():
    # The driver treats "%" as a placeholder marker, and the trigger message in the schema contains literal "%",
    # so it is doubled here. The file db/schema.sql itself stays exactly as the design gives it.
    op.get_bind().exec_driver_sql(SCHEMA.read_text(encoding="utf-8").replace("%", "%%"))


def downgrade():
    bind = op.get_bind()
    for statement in (
        "DROP VIEW IF EXISTS v_hallucination_current",
        "DROP TABLE IF EXISTS hallucination_reviews",
        "DROP TABLE IF EXISTS hallucination_events",
        "DROP TABLE IF EXISTS audit_traces",
        "DROP TABLE IF EXISTS intercepted_notes",
        "DROP FUNCTION IF EXISTS forbid_audit_mutation()",
        "DROP TYPE IF EXISTS hallucination_category",
        "DROP TYPE IF EXISTS risk_severity",
        "DROP TYPE IF EXISTS nli_class",
        "DROP TYPE IF EXISTS pipeline_stage",
    ):
        bind.exec_driver_sql(statement)
