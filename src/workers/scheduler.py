"""Scheduler wiring: turns the job registry into a running APScheduler.

This module owns only infrastructure — how jobs are scheduled and
started. The jobs themselves live under ``jobs/`` and self-gate on the
DB, so nothing here pauses or resumes jobs.
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from jobs.base import WorkerContext, run_job
from jobs.registry import build_job_specs

logger = logging.getLogger(__name__)


def build_scheduler(ctx: WorkerContext) -> AsyncIOScheduler:
    """Create the singleton scheduler with all jobs registered (not yet started)."""
    tz = ZoneInfo(ctx.settings.app_timezone)
    scheduler = AsyncIOScheduler(timezone=tz)

    for spec in build_job_specs(ctx.settings):
        scheduler.add_job(
            run_job,
            spec.trigger,
            args=(spec.id, spec.func, ctx),
            id=spec.id,
            name=spec.name,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=spec.misfire_grace_time,
            **spec.trigger_kwargs,
        )

    return scheduler
