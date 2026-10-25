"""add games.ended_at (post-game highlight grace window)

Revision ID: c1a2b3d4e5f6
Revises: d7e8f9a0b1c2
Create Date: 2026-10-25 12:00:00.000000

The poller short-circuits once a game is FINAL/OFF, but the NHL often
attaches a goal's highlight clip id several minutes after the game
ends — an overtime winner loses that race entirely, since FINAL lands
on the same poll as the goal. ``ended_at`` records when the terminal
state was first seen so the poller can keep polling for a
configurable grace window (FINAL_GRACE_MINUTES) before arming the
short-circuit.

Existing final rows are backfilled from ``updated_at`` so already-ended
games don't earn a surprise grace window (and its API calls) after
deploying.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a2b3d4e5f6"
down_revision: str | Sequence[str] | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "games",
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE games SET ended_at = updated_at WHERE game_state IN ('FINAL', 'OFF')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("games", "ended_at")
