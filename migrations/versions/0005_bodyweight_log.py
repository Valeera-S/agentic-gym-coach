"""bodyweight_log — a bodyweight time series with the measurement condition.

profile.bodyweight_kg is one overwritten value; Nutrition ch02 corrects every
calorie number with WEEKLY-AVERAGE bodyweight trends, which needs a series —
and a series whose readings can be compared like-for-like (a post-workout and
a fasted morning reading differ by more than a week's real change). Several
readings per date are allowed. Vocabularies (`condition`, `weight_unit`) are
VARCHAR validated by Pydantic (repo convention since 0002/0003). A purely
additive new table: no existing table or row is touched.

Downgrade refuses while the table holds rows — dropping it would destroy the
only copy of the user's readings.

Revision ID: 0005_bodyweight_log
Revises: 0004_decision_payload
Create Date: 2026-10-06
"""

from alembic import context, op

revision = "0005_bodyweight_log"
down_revision = "0004_decision_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE bodyweight_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            date DATE NOT NULL,
            weight_kg DOUBLE NOT NULL,
            weight_entered DOUBLE,
            weight_unit VARCHAR,
            condition VARCHAR NOT NULL,
            scale VARCHAR,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    msg = ("refusing to downgrade 0005: bodyweight_log holds bodyweight readings "
           "(the only copy) — export them first")
    if context.is_offline_mode():
        op.execute(
            "SELECT CASE WHEN EXISTS (SELECT 1 FROM bodyweight_log) "
            f"THEN error('{msg}') END"
        )
    else:
        n = op.get_bind().exec_driver_sql("SELECT count(*) FROM bodyweight_log").fetchone()[0]
        if n:
            raise RuntimeError(f"{msg} ({n} row(s))")
    op.execute("DROP TABLE bodyweight_log")
