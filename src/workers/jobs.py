"""Scheduled jobs. Each job takes a WorkerContext and returns a summary string."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from clients.bluesky_client import BlueskyClient
from clients.http_client import HttpClient
from db.models.game import Game
from repos.posts_repo import MAX_ATTEMPTS, PostsRepo
from workers.poll_worker import PollWorker

if TYPE_CHECKING:
    from workers.scheduler import WorkerContext

logger = logging.getLogger(__name__)


async def check_game_day(ctx: WorkerContext) -> str:
    """Daily gate: enable poll/poster intervals only when the team plays today.

    Resumes both intervals (with an immediate poll) on game days, pauses
    them otherwise so off-days cost one API call instead of polling all day.
    """
    from workers.scheduler import POLL_JOB_ID, POST_JOB_ID

    team = ctx.settings.nhl_team_abbreviation.strip().upper()
    if not team:
        return "skipped: NHL_TEAM_ABBREVIATION not set"

    game = await ctx.nhl.get_team_game_on_date(team_abbreviation=team)
    scheduler = ctx.scheduler

    def _set_paused(paused: bool, immediate_poll: bool = False) -> None:
        if scheduler is None:
            return
        for job_id in (POLL_JOB_ID, POST_JOB_ID):
            job = scheduler.get_job(job_id)
            if job is None:
                continue
            if paused:
                job.pause()
            else:
                job.resume()
                if immediate_poll and job_id == POLL_JOB_ID:
                    job.modify(next_run_time=datetime.now(UTC))

    if game is None or game.id is None:
        ctx.is_game_day = False
        _set_paused(True)
        return f"no {team} game today - poller paused"

    ctx.is_game_day = True
    _set_paused(False, immediate_poll=True)
    away = game.away_team.abbrev if game.away_team else "?"
    home = game.home_team.abbrev if game.home_team else "?"
    summary = f"game day: {away} @ {home} ({game.game_state}) - poller enabled"
    logger.info("check_game_day: %s", summary)
    return summary


async def poll_nhl_live(ctx: WorkerContext) -> str:
    """Every N min on game days: poll the followed team's game/goals/clips."""
    if ctx.is_game_day is False:
        return "skipped: not a game day (checker paused polling)"
    worker = PollWorker(
        nhl=ctx.nhl,
        sessions=ctx.sessions,
        team_abbreviation=ctx.settings.nhl_team_abbreviation,
    )
    return await worker.poll_once()


async def post_pending_clips(ctx: WorkerContext, *, force: bool = False) -> str:
    """Post the oldest unposted highlight clip to Bluesky (one per run).

    Runs every minute on game days. Claims the oldest ``posted=False`` row
    with ``FOR UPDATE SKIP LOCKED``, downloads the Brightcove MP4, posts it
    to Bluesky using the stored text, then marks the row as posted. Any
    failure leaves the row unposted so it is retried on the next run.

    ``force`` (manual runner only) skips the 4-hour posting-window check
    so late clips from a finished game can still be posted.
    """
    if ctx.is_game_day is False:
        return "skipped: not a game day"

    async with ctx.sessions() as session:
        posts = PostsRepo(session)
        pending = await posts.claim_pending_batch(limit=1)
        if not pending:
            await session.commit()
            return "no pending posts"

        post = pending[0]
        try:
            # Only post within [game_start, game_start + 4 hours].
            game = await session.get(Game, post.game_id)
            if game is None:
                await session.rollback()
                return f"clip {post.post_id}: no game row found"

            now = datetime.now(UTC)
            start = game.start_time
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            window_end = start + timedelta(hours=4)
            if not force:
                if now < start:
                    await session.rollback()
                    return f"clip {post.post_id}: before game start ({start})"
                if now > window_end:
                    await session.rollback()
                    return f"clip {post.post_id}: outside 4-hour posting window (ended {window_end})"

            # Resolve Brightcove video metadata to find the direct MP4 source.
            video = await ctx.nhl.brightcove.get_video(post.post_id)
            mp4 = next(
                (s for s in (video.sources or []) if s.container == "MP4"),
                None,
            )
            if mp4 is None or not mp4.src:
                return f"clip {post.post_id}: no MP4 rendition available"

            # Download the video bytes.
            async with HttpClient() as http:
                resp = await http.get(mp4.src, headers={"Accept": "*/*"})
                video_bytes = resp.content

            # Post to Bluesky using text already stored on the row.
            bsky = BlueskyClient()
            tag = (post.team or "NHL").strip().upper()
            await bsky.post_video(
                text=post.text,
                tag=tag,
                video=video_bytes,
                alt=f"NHL {tag} highlight",
            )

            await posts.mark_posted(post.post_id)
            await session.commit()
            return f"posted clip {post.post_id} ({len(video_bytes)} bytes)"

        except Exception as exc:
            await session.rollback()
            logger.exception("failed to post clip %s", post.post_id)

            # Record the failed attempt; give up after MAX_ATTEMPTS.
            post.attempts = (post.attempts or 0) + 1
            post.last_error = f"{type(exc).__name__}: {exc}"
            if post.attempts >= MAX_ATTEMPTS:
                post.defunct = True
            await session.commit()
            return f"clip {post.post_id}: post failed (attempt {post.attempts})"
