"""Background worker infrastructure: scheduler wiring and job registry."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config.settings import Settings
from services.nhl_service import NhlService
from workers.jobs import check_game_day, poll_nhl_live, post_pending_clips_stub

logger = logging.getLogger(__name__)

CHECK_JOB_ID = "game_day_check"
POLL_JOB_ID = "nhl_live_poller"
POST_JOB_ID = "poster"

JobFunc = Callable[["WorkerContext"], Awaitable[str]]


@dataclass
class WorkerContext:
    """Shared deps handed to every job. Built once at scheduler startup."""

    settings: Settings
    nhl: NhlService
    sessions: async_sessionmaker[AsyncSession]
    status: dict[str, dict[str, str | None]] = field(default_factory=dict)
    # Set by check_game_day; poll/poster jobs no-op when False.
    is_game_day: bool | None = None
    # Attached in build_scheduler so check_game_day can pause/resume jobs.
    scheduler: Any | None = None


async def run_job(name: str, func: JobFunc, ctx: WorkerContext) -> None:
    """Execute one job, isolating failures so one bad worker can't kill the rest."""
    try:
        summary = await func(ctx)
    except Exception as exc:
        logger.exception("worker %s failed", name)
        ctx.status[name] = {
            "last_run": datetime.now(UTC).isoformat(),
            "last_error": f"{type(exc).__name__}: {exc}",
            "summary": None,
        }
    else:
        ctx.status[name] = {
            "last_run": datetime.now(UTC).isoformat(),
            "last_error": None,
            "summary": summary,
        }


# Back-compat alias (run.py used to import the private name).
_run_job = run_job


def build_scheduler(ctx: WorkerContext) -> AsyncIOScheduler:
    """Create the singleton scheduler with all jobs registered (not yet started).

    - ``game_day_check``: daily cron, always active. Pauses/resumes the
      poll/poster intervals based on whether the team plays today.
    - ``nhl_live_poller`` / ``poster``: intervals, start paused. The checker
      (plus the startup check in workers.run) enables them on game days.
    """
    tz = ZoneInfo(ctx.settings.app_timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    ctx.scheduler = scheduler

    scheduler.add_job(
        run_job,
        "cron",
        hour=ctx.settings.game_day_check_hour,
        minute=ctx.settings.game_day_check_minute,
        args=(CHECK_JOB_ID, check_game_day, ctx),
        id=CHECK_JOB_ID,
        name="Game-day check (gate poll/poster)",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_job,
        "interval",
        minutes=ctx.settings.poll_interval_minutes,
        args=(POLL_JOB_ID, poll_nhl_live, ctx),
        id=POLL_JOB_ID,
        name="NHL live game poller (game days only)",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=240,
    )
    scheduler.add_job(
        run_job,
        "interval",
        minutes=ctx.settings.post_interval_minutes,
        args=(POST_JOB_ID, post_pending_clips_stub, ctx),
        id=POST_JOB_ID,
        name="Poster (stub, game days only)",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=240,
    )

    # Intervals stay dormant until the checker confirms a game day.
    # Must pause before start(); run.py re-evaluates immediately on boot.
    for job_id in (POLL_JOB_ID, POST_JOB_ID):
        job = scheduler.get_job(job_id)
        if job is not None:
            job.pause()

    return scheduler
