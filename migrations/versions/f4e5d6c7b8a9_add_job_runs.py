"""add job_runs (durable per-run job heartbeat)

Revision ID: f4e5d6c7b8a9
Revises: e1f2a3b4c5d6
Create Date: 2026-10-20 18:00:00.000000

Every scheduled run (success or failure) records one row here, so
"when did the poller/poster last run, did it work, what did it do" is
answerable from outside the scheduler process. Before this, that state
lived in an in-memory dict that nothing read: a dead worker and a
healthy idle worker were indistinguishable.

Retained for ``job_run_retention_days`` (default 7); pruning happens
opportunistically when new runs are recorded.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4e5d6c7b8a9"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_job_runs_started_at"), "job_runs", ["started_at"], unique=False
    )
    op.create_index(
        "ix_job_runs_job_id_started_at",
        "job_runs",
        ["job_id", sa.text("started_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_job_runs_job_id_started_at", table_name="job_runs")
    op.drop_index(op.f("ix_job_runs_started_at"), table_name="job_runs")
    op.drop_table("job_runs")
