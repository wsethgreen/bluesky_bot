"""add posts.next_attempt_at (per-post retry backoff)

Revision ID: e9f0a1b2c3d4
Revises: d4b6e8f2a1c3
Create Date: 2026-11-14 12:00:00.000000

A failed post used to be retried on the poster's very next run (once a
minute), so ``MAX_ATTEMPTS = 5`` burned through in about four minutes —
far shorter than a transient upstream error lasts. A Brightcove 403 on
a freshly uploaded clip (playback policy not yet propagated) cleared
within minutes, but the row had already gone defunct and the highlight
never posted.

``next_attempt_at`` pushes the next claim out by a growing delay after
each failure (3/6/12/24 minutes), so a post is only given up on after
~45 minutes of failures. NULL means "claimable now" and nothing is
backfilled: rows that already went defunct stay defunct (they are
recovered by hand), and rows still pending simply keep retrying on the
schedule they were on.

No index: the claim query reads ``posted``/``defunct`` first and the
whole table is a few hundred rows per season.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "d4b6e8f2a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "posts",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("posts", "next_attempt_at")
