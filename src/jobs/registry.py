"""Job registry: the single list of every scheduled job.

Each entry pairs a job function with its schedule. Adding a job means
adding one module under ``jobs/`` and one ``JobSpec`` here — nothing in
``workers/`` needs to change.

Jobs self-gate on the DB rather than being paused/resumed by a gate job,
so every job runs on its interval and cheaply no-ops when there's
nothing to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from config.settings import Settings
from jobs.base import JobFunc
from jobs.poll_nba_live import poll_nba_live
from jobs.poll_nhl_live import poll_nhl_live
from jobs.post_pending import post_pending


@dataclass(frozen=True)
class JobSpec:
    """How to run one job: its callable plus APScheduler trigger config."""

    id: str
    name: str
    func: JobFunc
    # APScheduler trigger type ("cron" or "interval") and its kwargs.
    trigger: str
    trigger_kwargs: dict[str, Any]
    misfire_grace_time: int


def build_job_specs(settings: Settings) -> list[JobSpec]:
    """Build the job specs for this process from config."""
    return [
        JobSpec(
            id="nhl_live_poller",
            name="NHL live game poller (self-gated to game days)",
            func=poll_nhl_live,
            trigger="interval",
            trigger_kwargs={"minutes": settings.poller.interval_minutes},
            misfire_grace_time=240,
        ),
        JobSpec(
            id="nba_live_poller",
            name="NBA live game poller (self-gated to game days)",
            func=poll_nba_live,
            trigger="interval",
            trigger_kwargs={"minutes": settings.nba.poll_interval_minutes},
            misfire_grace_time=240,
        ),
        JobSpec(
            id="poster",
            name="Poster (oldest pending text/video post in window)",
            func=post_pending,
            trigger="interval",
            trigger_kwargs={"minutes": settings.poster.interval_minutes},
            misfire_grace_time=240,
        ),
    ]


def staleness_thresholds(settings: Settings) -> dict[str, timedelta]:
    """How long each recorded job may go without a run before it looks stale.

    Only the jobs taskiq actually records in ``job_runs`` belong here.
    The live pollers run per game now (started by ``sync_today_games``),
    so they are silent on off-days by design and can't be judged this way.
    """
    return {"poster": timedelta(minutes=settings.poster.interval_minutes) * 3}
