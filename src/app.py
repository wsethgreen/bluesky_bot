"""FastAPI app."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from config.settings import get_settings
from db.engine import engine
from db.session import AsyncSessionLocal
from jobs.registry import staleness_thresholds
from repos.job_runs_repo import JobRunsRepo
from routes.nhl.goals import router as nhl_goals_router
from routes.nhl.players import router as nhl_players_router
from routes.nhl.schedule import router as nhl_schedule_router
from routes.nhl.teams import router as nhl_teams_router
from routes.nhl.videos import router as nhl_videos_router
from services.nhl_service import NhlService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_settings()
    app.state.nhl_service = NhlService()
    try:
        yield
    finally:
        service = app.state.nhl_service
        if service is not None:
            await service.client.http.aclose()
            await service.stats.http.aclose()
        await engine.dispose()


app = FastAPI(
    title="bluesky-bot",
    description="NHL bot API: schedules and highlights.",
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "system", "description": "Service health."},
        {"name": "nhl", "description": "Official NHL Web API."},
    ],
)


@app.get("/")
async def root(request: Request):
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["system"], summary="Service health")
async def health(request: Request) -> dict[str, object]:
    """Liveness probe, plus scheduler heartbeat read from the DB.

    Always 200 while the API process is alive — the workers run in the
    scheduler container, so their state is reported in the body
    (``workers_healthy``) but never gates this container's health. A
    run record older than ~3 intervals, or a failed latest run, makes
    ``workers_healthy`` false; a job that never ran shows as stale.
    """
    body: dict[str, object] = {"status": "ok"}
    try:
        async with AsyncSessionLocal() as session:
            repo = JobRunsRepo(session)
            latest = await repo.latest_per_job()
    except Exception:
        logger.exception("health: could not read job_runs")
        body["workers_healthy"] = None
        body["workers"] = {}
        return body

    by_job = {run.job_id: run for run in latest}
    now = datetime.now(UTC)
    thresholds = staleness_thresholds(get_settings())
    workers: dict[str, object] = {}
    healthy = True
    for job_id, threshold in thresholds.items():
        run = by_job.get(job_id)
        if run is None:
            workers[job_id] = {"last_run": None, "stale": True}
            healthy = False
            continue
        age = now - run.started_at
        stale = age > threshold
        healthy = healthy and not stale and run.ok
        workers[job_id] = {
            "last_run": run.started_at.isoformat(),
            "age_seconds": round(age.total_seconds()),
            "stale": stale,
            "ok": run.ok,
            "summary": run.summary,
            "error": run.error,
        }
    body["workers_healthy"] = healthy
    body["workers"] = workers
    return body


app.include_router(nhl_schedule_router)
app.include_router(nhl_teams_router)
app.include_router(nhl_videos_router)
app.include_router(nhl_goals_router)
app.include_router(nhl_players_router)
