"""drop posts_kind_check: kind is a plain string, constrained in code

Revision ID: a7d1c9e3f5b2
Revises: c1a2b3d4e5f6
Create Date: 2026-11-01 12:00:00.000000

The ``posts.kind`` check constraint duplicated knowledge the code
already enforces, and every new post kind (goal text, video,
period_end, penalty, ...) needed another migration just to extend it.
The constraint goes away entirely; the column widens to an unbounded
varchar in the same pass so no length limit sneaks back in. Valid
values are enforced in code instead: the ``PostKind`` enum plus the
typed ``PostsRepo.claim_pending(kind=...)`` parameter.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d1c9e3f5b2"
down_revision: str | Sequence[str] | None = "c1a2b3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("posts_kind_check", "posts", type_="check")
    op.alter_column(
        "posts",
        "kind",
        type_=sa.String(),
        existing_type=sa.String(20),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "posts",
        "kind",
        type_=sa.String(20),
        existing_type=sa.String(),
        existing_nullable=False,
    )
    # The narrowed constraint would reject penalty rows, so they go first.
    op.execute("DELETE FROM posts WHERE kind = 'penalty'")
    op.create_check_constraint(
        "posts_kind_check", "posts", "kind in ('text', 'video', 'period_end')"
    )
