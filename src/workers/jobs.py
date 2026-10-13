"""Scheduled jobs. Each job takes a WorkerContext and returns a summary string."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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


async def post_pending_clips_stub(ctx: WorkerContext) -> str:
    """Poster placeholder. No Bluesky interaction yet by design."""
    if ctx.is_game_day is False:
        return "skipped: not a game day"
    return "poster stub: 0 posted (not implemented)"
