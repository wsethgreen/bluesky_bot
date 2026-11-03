"""add games.league (label rows by league: nhl / nba)

Revision ID: b9c1d2e3f4a5
Revises: a7d1c9e3f5b2
Create Date: 2026-11-02 12:00:00.000000

The tables are shared by every league's poller: an NBA poller will write
the same ``games``/``posts`` rows the NHL poller does today. ``league``
labels each game so the poster can pick the right video resolver
(Brightcove for NHL, NBA content API for NBA) and queries can scope per
league without guessing from the team.

Every existing row is NHL. The column is added with
``server_default='nhl'`` — Postgres fills existing rows from the
default — and that default is dropped in the same migration so future
inserts must state their league explicitly: a forgotten ``league='nba'``
must fail loudly, not silently land in the NHL bucket.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9c1d2e3f4a5"
down_revision: str | Sequence[str] | None = "a7d1c9e3f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: add league (backfills 'nhl'), then drop the default."""
    op.add_column(
        "games",
        sa.Column(
            "league",
            sa.String(10),
            nullable=False,
            server_default="nhl",  # backfills existing rows, then goes away below
        ),
    )
    op.alter_column("games", "league", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("games", "league")
