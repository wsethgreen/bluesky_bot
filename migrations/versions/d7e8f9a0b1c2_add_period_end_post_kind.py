"""add period_end post kind (end-of-period scoreboard posts)

Revision ID: d7e8f9a0b1c2
Revises: f4e5d6c7b8a9
Create Date: 2026-10-24 12:00:00.000000

The poller queues an "End of N period." post when a play-by-play
``period-end`` event shows up, deduped on (game, event, kind) like the
goal posts. That needs a third value in the ``posts_kind_check``
constraint. The ``kind`` column also widens from VARCHAR(10) to
VARCHAR(20) in the same pass — 'period_end' exactly fills the old
width, so any future kind would otherwise need another migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e8f9a0b1c2"
down_revision: str | Sequence[str] | None = "f4e5d6c7b8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "posts",
        "kind",
        existing_type=sa.String(length=10),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
    op.drop_constraint("posts_kind_check", "posts", type_="check")
    op.create_check_constraint(
        "posts_kind_check", "posts", "kind in ('text', 'video', 'period_end')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("posts_kind_check", "posts", type_="check")
    # The narrowed constraint would reject these rows, so they go first.
    op.execute("DELETE FROM posts WHERE kind = 'period_end'")
    op.create_check_constraint("posts_kind_check", "posts", "kind in ('text', 'video')")
    op.alter_column(
        "posts",
        "kind",
        existing_type=sa.String(length=20),
        type_=sa.String(length=10),
        existing_nullable=False,
    )
