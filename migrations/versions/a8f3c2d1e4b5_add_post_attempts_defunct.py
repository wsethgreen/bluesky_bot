"""add post attempts, last_error, defunct

Revision ID: a8f3c2d1e4b5
Revises: 6eb2be89b62e
Create Date: 2026-10-17 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8f3c2d1e4b5"
down_revision: str | Sequence[str] | None = "6eb2be89b62e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "posts",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "posts",
        sa.Column("last_error", sa.String(), nullable=True),
    )
    op.add_column(
        "posts",
        sa.Column(
            "defunct",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(op.f("ix_posts_defunct"), "posts", ["defunct"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_posts_defunct"), table_name="posts")
    op.drop_column("posts", "defunct")
    op.drop_column("posts", "last_error")
    op.drop_column("posts", "attempts")
