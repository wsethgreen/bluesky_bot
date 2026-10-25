"""Bluesky publishing job: post the oldest pending text/video post.

Runs every minute on game days. Claims the oldest ``posted=False`` row
with ``FOR UPDATE SKIP LOCKED``. Text rows (goal announcements and
end-of-period scoreboards) go out immediately. Video rows resolve the
Brightcove MP4, download it,
and post it with the short caption — quoting the goal's text post when
that post is already up, standalone otherwise. Rows are claimed
oldest-first and a goal's text row always precedes its video row, so the
quote is normally available by the time the video posts. Any failure
leaves the row unposted so it is retried on the next run.

When ``env == "local"`` nothing is sent to Bluesky: the post's content
(text, or caption + highlight URL) is logged instead, and the row is
consumed with a synthetic ``local://`` ref so the queue keeps draining
and nothing re-logs on the next run. Brightcove and Bluesky credentials
are not touched in that mode.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from clients.bluesky_client import BlueskyClient
from clients.http_client import HttpClient
from db.models.game import Game
from models.bluesky import BlueskyPostRef
from models.enum import PostKind
from repos.posts_repo import MAX_ATTEMPTS, PostsRepo

if TYPE_CHECKING:
    from db.models.post import Post
    from jobs.base import WorkerContext

logger = logging.getLogger(__name__)

JOB_ID = "poster"


async def post_pending(ctx: WorkerContext, *, force: bool = False) -> str:
    """Post the oldest pending Bluesky post (text or video), one per run.

    Non-video rows — goal text and period-end scoreboards alike — share
    the plain-text branch below; only video rows need clip resolution.

    ``force`` (manual runner only) skips the 4-hour posting-window check
    so late posts from a finished game can still go out.

    ``ctx.settings.env == "local"`` logs the post instead of publishing
    it (see module docstring); the window checks still apply, so ``force``
    remains the way to drain rows from outside the window.

    No game-day gate: off-days with nothing pending cost one indexed
    read. Pending rows outside their window are claimed and rolled back
    (no external calls), so this needs no separate off-day short-circuit.
    """
    async with ctx.sessions() as session:
        posts = PostsRepo(session)
        pending = await posts.claim_pending_batch(limit=1)
        if not pending:
            await session.commit()
            return "no pending posts"

        post = pending[0]
        label = f"{post.kind} post {post.id}"
        try:
            # Only post within [game_start, game_start + 4 hours].
            game = await session.get(Game, post.game_id)
            if game is None:
                await session.rollback()
                return f"{label}: no game row found"

            now = datetime.now(UTC)
            start = game.start_time
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            window_end = start + timedelta(hours=4)
            if not force:
                if now < start:
                    await session.rollback()
                    return f"{label}: before game start ({start})"
                if now > window_end:
                    await session.rollback()
                    return (
                        f"{label}: outside 4-hour posting window (ended {window_end})"
                    )

            tag = (post.team or "NHL").strip().upper()

            if ctx.settings.env.strip().lower() == "local":
                # Dry run: log what would go out instead of calling
                # Bluesky, then consume the row with a synthetic local://
                # ref so the queue drains instead of re-logging this row
                # on every run.
                detail = _would_post_detail(post)
                await posts.mark_posted(post.id, uri=f"local://{post.kind}/{post.id}")
                await session.commit()
                logger.info("local env: would post %s: %s", label, detail)
                return f"{label}: local env, logged only ({detail})"

            bsky = BlueskyClient()

            if post.kind == PostKind.VIDEO:
                if post.clip_id is None:
                    raise ValueError("video row has no clip_id")
                quote = await _quoted_text_ref(posts, post)

                # Resolve Brightcove video metadata to find the direct MP4.
                video = await ctx.nhl.brightcove.get_video(post.clip_id)
                mp4 = next(
                    (s for s in (video.sources or []) if s.container == "MP4"),
                    None,
                )
                if mp4 is None or not mp4.src:
                    return f"{label}: no MP4 rendition available"

                # Download the video bytes.
                async with HttpClient() as http:
                    resp = await http.get(mp4.src, headers={"Accept": "*/*"})
                    video_bytes = resp.content

                ref = await bsky.post_video(
                    text=post.text,
                    tag=tag,
                    video=video_bytes,
                    alt=f"NHL {tag} highlight",
                    quote=quote,
                )
            else:
                ref = await bsky.post_text(text=post.text, tag=tag)

            await posts.mark_posted(post.id, uri=ref.uri, cid=ref.cid)
            await session.commit()
            return f"posted {label} ({ref.uri})"

        except Exception as exc:
            await session.rollback()
            logger.exception("failed to post %s", label)

            # Record the failed attempt; give up after MAX_ATTEMPTS.
            post.attempts = (post.attempts or 0) + 1
            post.last_error = f"{type(exc).__name__}: {exc}"
            if post.attempts >= MAX_ATTEMPTS:
                post.defunct = True
            await session.commit()
            return f"{label}: post failed (attempt {post.attempts})"


def _would_post_detail(post: Post) -> str:
    """Log-ready description of what a local-env run would have published.

    Video rows carry the highlight URL captured at claim time, so the
    dry run never needs Brightcove to say what would go out.
    """
    if post.kind == PostKind.VIDEO:
        return (
            f"video caption {post.text!r} (clip {post.clip_id}), "
            f"highlight {post.highlight_url or '(no highlight url)'}"
        )
    return f"text {post.text!r}"


async def _quoted_text_ref(posts: PostsRepo, post: Post) -> BlueskyPostRef | None:
    """Strong ref of the goal's text post, when it is already published.

    Video rows normally follow their text row (older claim order), so the
    ref is usually available. ``None`` means "post the video standalone":
    the text post may still be retrying, went defunct, or the row is a
    legacy pre-migration video post with no text sibling.
    """
    ref = await posts.get_posted_text_ref(post.game_id, post.event_id)
    return None if ref is None else BlueskyPostRef(uri=ref[0], cid=ref[1])
