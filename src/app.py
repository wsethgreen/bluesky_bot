"""FastAPI app."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from config.settings import get_settings
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
    """Liveness probe."""
    scheduler = getattr(request.app.state, "scheduler", None)
    return {
        "status": "ok",
        "workers_running": scheduler is not None
        and getattr(scheduler, "running", False),
    }


app.include_router(nhl_schedule_router)
app.include_router(nhl_teams_router)
app.include_router(nhl_videos_router)
app.include_router(nhl_goals_router)
app.include_router(nhl_players_router)
