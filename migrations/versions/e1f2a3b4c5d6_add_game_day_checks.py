"""add game_day_checks (cached negative schedule lookups)

Revision ID: e1f2a3b4c5d6
Revises: c3f8d2a91e77
Create Date: 2026-10-20 12:00:00.000000

The poller cannot tell "no game today" from "game not yet discovered"
by looking at ``games`` alone — it is what creates the game row. This
table caches the negative answer ("checked: no game") keyed by
(team, day) so off-days cost one schedule call per day instead of one
per poll interval. The date key expires the cache at midnight without
TTL logic.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "c3f8d2a91e77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "game_day_checks",
        sa.Column("team", sa.String(length=10), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("has_game", sa.Boolean(), nullable=False),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("team", "day"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("game_day_checks")
