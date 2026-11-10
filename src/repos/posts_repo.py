"""Postgres access for the posts table (surrogate PK: id)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.game import Game
from db.models.post import Post
from models.enum import PostKind

# Give up on a post after this many failed attempts.
MAX_ATTEMPTS = 5


class PostsRepo:
    """Pending/posted Bluesky posts (one row per post to make).

    Deduped by the (game_id, event_id, kind) unique key, so re-polls
    never queue the same goal's text (or video) post twice.

    Owns no transactions: methods flush but never commit, so callers
    (workers, routes) control commit/rollback boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim_pending(
        self,
        *,
        kind: PostKind,
        game_id: int,
        team: str,
        event_id: int | None = None,
        text: str = "",
        clip_id: int | None = None,
        highlight_url: str | None = None,
    ) -> bool:
        """Insert as unposted if new. Returns True when inserted, False if seen.

        ``kind`` is typed as ``PostKind`` on purpose: the DB column is
        a plain unconstrained string, so this parameter is where valid
        values are enforced.
        """
        result = await self.session.execute(
            pg_insert(Post)
            .values(
                kind=kind,
                game_id=game_id,
                event_id=event_id,
                team=team,
                text=text,
                clip_id=clip_id,
                highlight_url=highlight_url,
                posted=False,
            )
            .on_conflict_do_nothing(constraint="posts_game_event_kind_key")
        )
        return result.rowcount == 1

    async def existing_event_ids(self, game_id: int, kind: PostKind) -> set[int]:
        """Event ids already queued for (game, kind) — the dedup check.

        Lets a caller skip expensive enrichment (an NBA boxscore fetch,
        say) when every post it would claim already exists, instead of
        discovering that only after the insert conflict.
        """
        result = await self.session.execute(
            select(Post.event_id).where(
                Post.game_id == game_id,
                Post.kind == kind,
                Post.event_id.is_not(None),
            )
        )
        return {event_id for (event_id,) in result}

    async def get_post(self, post_id: int) -> Post | None:
        return await self.session.get(Post, post_id)

    async def list_pending(self, limit: int = 25) -> list[Post]:
        """Oldest unposted rows, no locking (read-only views)."""
        result = await self.session.execute(
            select(Post)
            .where(Post.posted.is_(False))
            .where(Post.defunct.is_(False))
            .order_by(Post.created_at, Post.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def claim_pending_batch(
        self, limit: int = 10, *, window_hours: int, force: bool = False
    ) -> list[Post]:
        """Lock the oldest claimable unposted, non-defunct rows for this consumer.

        Skips rows already marked ``defunct`` or that have exhausted
        ``MAX_ATTEMPTS`` retries. ``FOR UPDATE OF posts SKIP LOCKED`` so
        competing poster consumers never grab the same row — only the
        ``posts`` row is locked, never the joined ``games`` row (the
        transaction stays open across the video download, and the
        poller upserts ``games`` every cycle). The ``created_at, id``
        order (text rows are claimed before a goal's video row) is what
        makes the goal text post go out before its highlight video.

        Unless ``force``, a row is only claimable while its game's
        start time sits inside ``[now - window_hours, now]`` — its
        posting window (pre-game rows wait for kickoff too). Stale rows
        awaiting a ``--force`` backfill are therefore never locked, so
        they cannot block newer in-window rows behind them in the
        queue.

        Callers must commit (or roll back) to release.
        """
        stmt = (
            select(Post)
            .where(Post.posted.is_(False))
            .where(Post.defunct.is_(False))
            .where(Post.attempts < MAX_ATTEMPTS)
        )
        if not force:
            now = datetime.now(UTC)
            # start <= now <= start + window_hours, rewritten as a
            # pure timestamp range to avoid interval arithmetic.
            stmt = stmt.join(Game, Game.game_id == Post.game_id).where(
                Game.start_time <= now,
                Game.start_time >= now - timedelta(hours=window_hours),
            )
        stmt = stmt.order_by(Post.created_at, Post.id).limit(limit)
        result = await self.session.execute(
            stmt.with_for_update(of=Post, skip_locked=True)
        )
        return list(result.scalars().all())

    async def count_pending(self) -> int:
        """Unposted, non-defunct rows still eligible to be claimed.

        Counts regardless of window, so callers can tell "queue empty"
        apart from "rows waiting outside their window" (which only a
        ``--force`` run will drain).
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Post)
            .where(Post.posted.is_(False))
            .where(Post.defunct.is_(False))
            .where(Post.attempts < MAX_ATTEMPTS)
        )
        return int(result.scalar_one())

    async def get_posted_text_ref(
        self, game_id: int, event_id: int | None
    ) -> tuple[str, str] | None:
        """The sibling text post's ``(uri, cid)`` once it has been posted.

        Used by the poster to quote the goal text on the highlight video
        (``app.bsky.embed.recordWithMedia``). Returns ``None`` when the
        text post is still pending, went defunct, or predates this schema.
        """
        if event_id is None:
            return None
        result = await self.session.execute(
            select(Post.posted_uri, Post.posted_cid)
            .where(Post.game_id == game_id)
            .where(Post.event_id == event_id)
            .where(Post.kind == PostKind.TEXT)
            .where(Post.posted.is_(True))
            .where(Post.posted_uri.is_not(None))
            .where(Post.posted_cid.is_not(None))
            .limit(1)
        )
        row = result.one_or_none()
        return None if row is None else (row[0], row[1])

    async def mark_posted(
        self, post_id: int, *, uri: str | None = None, cid: str | None = None
    ) -> bool:
        """Flag a post as published, recording its strong ref.

        Returns False when the row doesn't exist.
        """
        post = await self.session.get(Post, post_id)
        if post is None:
            return False
        post.posted = True
        post.posted_at = datetime.now(UTC)
        post.posted_uri = uri
        post.posted_cid = cid
        return True

    async def mark_defunct(self, post_id: int, error: str) -> bool:
        """Mark a post as defunct (give up) with the last error recorded.

        Returns False when the row doesn't exist.
        """
        post = await self.session.get(Post, post_id)
        if post is None:
            return False
        post.defunct = True
        post.last_error = error
        return True
