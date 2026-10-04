"""Background worker infrastructure: scheduler wiring and job registry."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config.settings import Settings
from services.nhl_service import NhlService
from workers.jobs import poll_nhl_live

logger = logging.getLogger(__name__)

POLL_INTERVAL_MINUTES = 5

JobFunc = Callable[["WorkerContext"], Awaitable[str]]


@dataclass
class WorkerContext:
    """Shared deps handed to every job. Built once in lifespan."""

    settings: Settings
    nhl: NhlService
    sessions: async_sessionmaker[AsyncSession]
    status: dict[str, dict[str, str | None]] = field(default_factory=dict)


async def _run_job(name: str, func: JobFunc, ctx: WorkerContext) -> None:
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


def build_scheduler(ctx: WorkerContext) -> AsyncIOScheduler:
    """Create the scheduler with all jobs registered (not yet started)."""
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        _run_job,
        "interval",
        minutes=POLL_INTERVAL_MINUTES,
        args=("nhl_live_poller", poll_nhl_live, ctx),
        id="nhl_live_poller",
        name="NHL live game poller",
        # First run fires immediately so restarts don't idle until the next tick.
        next_run_time=datetime.now(UTC),
        coalesce=True,
        max_instances=1,
        misfire_grace_time=240,
    )
    return scheduler
