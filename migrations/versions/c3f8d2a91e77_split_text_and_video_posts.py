"""split text and video posts (surrogate PK, kind column)

Revision ID: c3f8d2a91e77
Revises: a8f3c2d1e4b5
Create Date: 2026-10-18 12:00:00.000000

The posts table used the Brightcove clip id as its primary key, so a
goal could only ever queue one post and only once a highlight clip
existed. This gives the table a surrogate PK and a ``kind`` column so
each goal can queue two independent posts: a 'text' post at detection
and a 'video' post when the clip id shows up.

Legacy rows are all video posts: they keep their clip id in the new
nullable unique ``clip_id`` column and default to kind='video'.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8d2a91e77"
down_revision: str | Sequence[str] | None = "a8f3c2d1e4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The old dedupe key was the clip id alone, so a goal whose clip id
    # was replaced between polls could leave two rows for one event.
    # Collapse those before adding the (game, event, kind) unique key,
    # preferring rows already posted, then the oldest. (Uses the pre-
    # rename ``post_id`` column: this runs before the surrogate PK.)
    op.execute(
        """
        DELETE FROM posts
        WHERE post_id IN (
            SELECT post_id FROM (
                SELECT post_id, row_number() OVER (
                    PARTITION BY game_id, event_id
                    ORDER BY posted DESC, created_at, post_id
                ) AS rn
                FROM posts
            ) ranked
            WHERE rn > 1
        )
        """
    )

    # Surrogate PK: BIGSERIAL backfills existing rows on add.
    op.execute("ALTER TABLE posts ADD COLUMN id BIGSERIAL NOT NULL")

    # clip id moves out of the PK into a nullable unique column.
    op.execute("ALTER TABLE posts DROP CONSTRAINT posts_pkey")
    op.execute("ALTER TABLE posts RENAME COLUMN post_id TO clip_id")
    op.alter_column("posts", "clip_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_unique_constraint("uq_posts_clip_id", "posts", ["clip_id"])
    op.execute("ALTER TABLE posts ADD PRIMARY KEY (id)")

    # Legacy rows are all video posts; the app sets kind explicitly on
    # every insert, so the server default is dropped after the backfill.
    op.add_column(
        "posts",
        sa.Column("kind", sa.String(length=10), nullable=False, server_default="video"),
    )
    op.alter_column("posts", "kind", server_default=None)
    op.create_check_constraint("posts_kind_check", "posts", "kind in ('text', 'video')")
    op.create_unique_constraint(
        "posts_game_event_kind_key", "posts", ["game_id", "event_id", "kind"]
    )

    # Strong ref of the published post, so a video row can quote its
    # sibling text row via app.bsky.embed.recordWithMedia.
    op.add_column("posts", sa.Column("posted_uri", sa.String(), nullable=True))
    op.add_column("posts", sa.Column("posted_cid", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("posts", "posted_cid")
    op.drop_column("posts", "posted_uri")

    op.drop_constraint("posts_game_event_kind_key", "posts", type_="unique")
    op.drop_constraint("posts_kind_check", "posts", type_="check")

    # Text rows have no clip id, so they cannot survive the clip-id PK.
    op.execute("DELETE FROM posts WHERE kind = 'text'")
    op.drop_column("posts", "kind")

    op.execute("ALTER TABLE posts DROP CONSTRAINT posts_pkey")
    op.drop_constraint("uq_posts_clip_id", "posts", type_="unique")
    op.execute("ALTER TABLE posts RENAME COLUMN clip_id TO post_id")
    op.alter_column("posts", "post_id", existing_type=sa.BigInteger(), nullable=False)
    op.execute("ALTER TABLE posts ADD PRIMARY KEY (post_id)")
    op.drop_column("posts", "id")
