"""add teams.league and scope tri-code uniqueness per league

Revision ID: d4b6e8f2a1c3
Revises: b9c1d2e3f4a5
Create Date: 2026-11-06 12:00:00.000000

The ``teams`` table outgrew its single-league design once the NBA
poller started writing rows:

- tri-codes repeat across leagues — ESPN and the NHL both use BOS, CHI,
  DAL, DET, MTL, PHI, TOR, UTA — so the bare ``UNIQUE(tri_code)`` would
  reject the first Celtics/Utah/etc. row the NBA poller stores as an
  opponent. Uniqueness moves to ``(league, tri_code)``.
- ``league`` labels each row, mirroring ``games.league``
  (b9c1d2e3f4a5): added with ``server_default='nhl'`` so Postgres
  backfills existing rows, then the default is dropped so every insert
  states its league explicitly.

NBA rows also carry a namespaced primary key (ESPN's id + an offset,
see ``nba_team_row_id`` in ``db/models/team.py``) — that one is pure
Python, no schema change needed here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b6e8f2a1c3"
down_revision: str | Sequence[str] | None = "b9c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add league (backfills 'nhl'), swap unique constraints."""
    op.add_column(
        "teams",
        sa.Column(
            "league",
            sa.String(10),
            nullable=False,
            server_default="nhl",  # backfills existing rows, then goes away below
        ),
    )
    op.alter_column("teams", "league", server_default=None)
    # Composite unique first: every existing row is 'nhl' with globally
    # unique codes, so it holds before the narrower global one goes away.
    op.create_unique_constraint(
        "uq_teams_league_tri_code", "teams", ["league", "tri_code"]
    )
    # Unnamed in the initial migration → Postgres' teams_tri_code_key.
    op.drop_constraint("teams_tri_code_key", "teams", type_="unique")


def downgrade() -> None:
    """Restore the global unique on tri_code.

    Assumes NBA rows are gone first: the restored constraint cannot
    hold alongside an NBA team sharing a code with an NHL one (delete
    nba games/teams before downgrading).
    """
    op.create_unique_constraint("teams_tri_code_key", "teams", ["tri_code"])
    op.drop_constraint("uq_teams_league_tri_code", "teams", type_="unique")
    op.drop_column("teams", "league")
