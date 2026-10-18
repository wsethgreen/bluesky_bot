"""Postgres access for the posts table (PK: post_id INTEGER)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.post import Post

# Give up on a clip after this many failed post attempts.
MAX_ATTEMPTS = 5


class PostsRepo:
    """Pending/posted highlight clips. The PK dedupes re-polls.

    Owns no transactions: methods flush but never commit, so callers
    (workers, routes) control commit/rollback boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim_pending(
        self,
        clip_id: int,
        game_id: int,
        team: str,
        event_id: int | None = None,
        text: str = "",
        highlight_url: str | None = None,
    ) -> bool:
        """Insert as unposted if new. Returns True when inserted, False if seen."""
        result = await self.session.execute(
            pg_insert(Post)
            .values(
                post_id=clip_id,
                game_id=game_id,
                event_id=event_id,
                team=team,
                text=text,
                highlight_url=highlight_url,
                posted=False,
            )
            .on_conflict_do_nothing(index_elements=["post_id"])
        )
        return result.rowcount == 1

    async def get_post(self, clip_id: int) -> Post | None:
        return await self.session.get(Post, clip_id)

    async def list_pending(self, limit: int = 25) -> list[Post]:
        """Oldest unposted clips, no locking (read-only views)."""
        result = await self.session.execute(
            select(Post)
            .where(Post.posted.is_(False))
            .where(Post.defunct.is_(False))
            .order_by(Post.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def claim_pending_batch(self, limit: int = 10) -> list[Post]:
        """Lock the oldest unposted, non-defunct rows for this consumer.

        Skips rows already marked ``defunct`` or that have exhausted
        ``MAX_ATTEMPTS`` retries. ``FOR UPDATE SKIP LOCKED`` so competing
        poster consumers never grab the same clip. Callers must commit (or
        roll back) to release.
        """
        result = await self.session.execute(
            select(Post)
            .where(Post.posted.is_(False))
            .where(Post.defunct.is_(False))
            .where(Post.attempts < MAX_ATTEMPTS)
            .order_by(Post.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())

    async def mark_posted(self, clip_id: int) -> bool:
        """Flag a clip as posted. Returns False when the row doesn't exist."""
        post = await self.session.get(Post, clip_id)
        if post is None:
            return False
        post.posted = True
        post.posted_at = datetime.now(UTC)
        return True

    async def mark_defunct(self, clip_id: int, error: str) -> bool:
        """Mark a clip as defunct (give up) with the last error recorded.

        Returns False when the row doesn't exist.
        """
        post = await self.session.get(Post, clip_id)
        if post is None:
            return False
        post.defunct = True
        post.last_error = error
        return True
