"""decision_log.payload — a structured snapshot column for the audit trail.

The session amend / delete tools must write the complete pre-change session
row to the audit trail in a form it can be restored from. The 0001 columns
are free text (trigger_signal, reasoning_chain, ...), so this adds a JSON
`payload` column (NULL on every existing row and on audit entries that carry
no snapshot). A plain ADD COLUMN: decision_log is not rebuilt and no existing
value changes.

Downgrade refuses while any payload is stored — dropping it would destroy the
only copy of an amended or deleted session.

Revision ID: 0004_decision_payload
Revises: 0003_vocab_exercise_fields
Create Date: 2026-10-05
"""

from alembic import context, op

revision = "0004_decision_payload"
down_revision = "0003_vocab_exercise_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE decision_log ADD COLUMN payload JSON")


def downgrade() -> None:
    msg = ("refusing to downgrade 0004: decision_log.payload holds session snapshots "
           "(the only copy of amended/deleted sessions) — export them first")
    if context.is_offline_mode():
        op.execute(
            "SELECT CASE WHEN EXISTS (SELECT 1 FROM decision_log WHERE payload IS NOT NULL) "
            f"THEN error('{msg}') END"
        )
    else:
        n = op.get_bind().exec_driver_sql(
            "SELECT count(*) FROM decision_log WHERE payload IS NOT NULL").fetchone()[0]
        if n:
            raise RuntimeError(f"{msg} ({n} row(s))")
    op.execute("ALTER TABLE decision_log DROP COLUMN payload")
