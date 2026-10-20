"""Shared job execution framework: context, runner, and lifecycle.

Jobs are plain async functions ``(WorkerContext) -> str`` that do their
own work and return a human-readable summary. This module owns only the
lifecycle concerns every job shares — dependency access, failure
isolation, and run recording — never job-specific behavior.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config.settings import Settings
from repos.job_runs_repo import JobRunsRepo
from services.nhl_service import NhlService

logger = logging.getLogger(__name__)

JobFunc = Callable[["WorkerContext"], Awaitable[str]]

# Cap the stored error so one pathologically long message can't bloat
# a job_runs row.
_MAX_ERROR_CHARS = 2000


@dataclass
class WorkerContext:
    """Shared deps handed to every job. Built once at scheduler startup."""

    settings: Settings
    nhl: NhlService
    sessions: async_sessionmaker[AsyncSession]


async def run_job(name: str, func: JobFunc, ctx: WorkerContext) -> None:
    """Execute one job, isolating failures so one bad worker can't kill the rest.

    Every run — success or failure — is recorded in ``job_runs`` so the
    scheduler's heartbeat survives the process and can be read from
    anywhere (``workers.manual status``, ``/health``). A job that stops
    leaving rows is the real alarm; a recorded failure is just history.
    """
    started = datetime.now(UTC)
    try:
        summary = await func(ctx)
    except Exception as exc:
        logger.exception("worker %s failed", name)
        await _record_run(
            ctx,
            name,
            started,
            ok=False,
            summary=None,
            error=f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS],
        )
    else:
        await _record_run(ctx, name, started, ok=True, summary=summary, error=None)


async def _record_run(
    ctx: WorkerContext,
    name: str,
    started: datetime,
    *,
    ok: bool,
    summary: str | None,
    error: str | None,
) -> None:
    """Persist one run's outcome. Never raises: observability can't break the worker.

    Also opportunistically prunes old rows (once an hour at the top of
    the hour, per job) so the table stays bounded without a dedicated
    housekeeping job.
    """
    finished = datetime.now(UTC)
    try:
        async with ctx.sessions() as session:
            repo = JobRunsRepo(session)
            await repo.record(
                name,
                started_at=started,
                finished_at=finished,
                ok=ok,
                summary=summary,
                error=error,
            )
            await session.commit()
            if finished.minute == 0:
                removed = await repo.prune(
                    older_than=finished
                    - timedelta(days=ctx.settings.job_run_retention_days)
                )
                if removed:
                    logger.info("pruned %d old job_runs rows", removed)
    except Exception:
        logger.exception("failed to record run for %s", name)
