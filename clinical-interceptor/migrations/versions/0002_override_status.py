"""Clinician override: one new note status, OVERRIDDEN_BY_CLINICIAN.

Approved by the project owner on 2026-10-20 (Phase 3 brief). The change is recorded in
docs/api-changes/2026-10-20-override.md. db/schema.sql stays as the design gives it, and this revision moves the
database on from it. The override itself is an append-only row in hallucination_reviews (DATABASE.md 4), so no audit
table changes. db/schema.sql now carries the same status value, so databases built from it match this revision.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

OLD = "('RECEIVED', 'VERIFIED', 'BLOCKED', 'RELEASED', 'FAILED')"
NEW = "('RECEIVED', 'VERIFIED', 'BLOCKED', 'RELEASED', 'FAILED', 'OVERRIDDEN_BY_CLINICIAN')"


def upgrade():
    op.execute("ALTER TABLE intercepted_notes DROP CONSTRAINT IF EXISTS intercepted_notes_pipeline_status_check")
    op.execute(f"ALTER TABLE intercepted_notes ADD CONSTRAINT intercepted_notes_pipeline_status_check "
               f"CHECK (pipeline_status IN {NEW})")


def downgrade():
    # A note overridden under 0002 goes back to BLOCKED. Its review rows stay, because they are append-only.
    op.execute("UPDATE intercepted_notes SET pipeline_status = 'BLOCKED' "
               "WHERE pipeline_status = 'OVERRIDDEN_BY_CLINICIAN'")
    op.execute("ALTER TABLE intercepted_notes DROP CONSTRAINT IF EXISTS intercepted_notes_pipeline_status_check")
    op.execute(f"ALTER TABLE intercepted_notes ADD CONSTRAINT intercepted_notes_pipeline_status_check "
               f"CHECK (pipeline_status IN {OLD})")
