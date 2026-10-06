"""sessions.label — an optional, user-chosen name for a recurring workout
("back day", "Push", "Full body A"). Lets the coach find "the same session as
last time" (UX3). Stored as entered; matching is lookup_key-normalized in code.
Purely additive and nullable: no existing row changes.

Downgrade refuses while any session carries a label — dropping the column
would destroy the user's labels silently.

Revision ID: 0006_session_label
Revises: 0005_bodyweight_log
Create Date: 2026-10-06
"""

from alembic import context, op

revision = "0006_session_label"
down_revision = "0005_bodyweight_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN label VARCHAR")


def downgrade() -> None:
    msg = ("refusing to downgrade 0006: sessions carry labels "
           "(they would be lost) — clear them first")
    if context.is_offline_mode():
        op.execute("SELECT CASE WHEN EXISTS (SELECT 1 FROM sessions WHERE label IS NOT NULL) "
                   f"THEN error('{msg}') END")
    else:
        n = op.get_bind().exec_driver_sql(
            "SELECT count(*) FROM sessions WHERE label IS NOT NULL").fetchone()[0]
        if n:
            raise RuntimeError(f"{msg} ({n} row(s))")
    op.execute("ALTER TABLE sessions DROP COLUMN label")
