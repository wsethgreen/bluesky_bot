"""Scheduled jobs. Each job takes a WorkerContext and returns a summary string."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workers.poll_worker import PollWorker

if TYPE_CHECKING:
    from workers.scheduler import WorkerContext


async def poll_nhl_live(ctx: WorkerContext) -> str:
    """Every 5 min: poll the followed team's game, goals, and clips."""
    worker = PollWorker(
        nhl=ctx.nhl,
        sessions=ctx.sessions,
        team_abbreviation=ctx.settings.nhl_team_abbreviation,
    )
    return await worker.poll_once()
