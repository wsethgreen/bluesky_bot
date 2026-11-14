"""Shared FastAPI dependencies (wired to app.state in lifespan)."""

from __future__ import annotations

from fastapi import HTTPException, Request

from services.nba_service import NbaService
from services.nhl_service import NhlService


def get_nhl_service(request: Request) -> NhlService:
    # Resolved at request time so lifespan has populated state.
    # Tests can bypass via app.dependency_overrides.
    service: NhlService | None = getattr(request.app.state, "nhl_service", None)
    if service is None:
        raise HTTPException(status_code=500, detail="NHL service not configured")
    return service


def get_nba_service(request: Request) -> NbaService:
    # Same request-time resolution as get_nhl_service.
    service: NbaService | None = getattr(request.app.state, "nba_service", None)
    if service is None:
        raise HTTPException(status_code=500, detail="NBA service not configured")
    return service
