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
    """Build the job specs for this process from settings."""
    return [
        JobSpec(
            id="nhl_live_poller",
            name="NHL live game poller (self-gated to game days)",
            func=poll_nhl_live,
            trigger="interval",
            trigger_kwargs={"minutes": settings.poll_interval_minutes},
            misfire_grace_time=240,
        ),
        JobSpec(
            id="poster",
            name="Poster (oldest pending text/video post in window)",
            func=post_pending,
            trigger="interval",
            trigger_kwargs={"minutes": settings.post_interval_minutes},
            misfire_grace_time=240,
        ),
    ]


def staleness_thresholds(settings: Settings) -> dict[str, timedelta]:
    """How long each job may go without a recorded run before its absence means trouble.

    3x the interval: two consecutive missed runs still look like a
    hiccup (slow start, one misfire within grace); the third is an
    outage. Only interval-triggered jobs are covered — a cron-triggered
    job needs an explicit threshold here.
    """
    return {
        spec.id: timedelta(minutes=spec.trigger_kwargs["minutes"]) * 3
        for spec in build_job_specs(settings)
        if spec.trigger == "interval"
    }
