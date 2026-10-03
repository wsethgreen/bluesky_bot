"""NHL schedule routes (official NHL Web API)."""

from __future__ import annotations

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from models.nhl import NhlScheduleGame, NhlScheduleResponse
from routes.dependencies import get_nhl_service
from services.nhl_service import NhlService

router = APIRouter(prefix="/nhl/schedule", tags=["nhl"])

_ERROR_RESPONSES = {
    422: {"description": "Invalid date format (expected YYYY-MM-DD)."},
    502: {"description": "NHL API returned an error status."},
    503: {"description": "NHL API unreachable (network/timeout)."},
}


@router.get(
    "/team",
    response_model=NhlScheduleGame,
    summary="Team's game on a date",
    responses={
        **_ERROR_RESPONSES,
        404: {"description": "The team has no game on that date."},
    },
)
async def get_team_game(
    date: str | None = Query(
        default=None,
        description="Date in YYYY-MM-DD format. Defaults to today.",
        examples=["2023-11-10"],
    ),
    team_abbreviation: str | None = Query(
        default=None,
        description="Team abbreviation. Defaults to NHL_TEAM_ABBREVIATION.",
        examples=["CBJ"],
    ),
    service: NhlService = Depends(get_nhl_service),  # noqa: B008
) -> NhlScheduleGame:
    """Find the team's game on ``date``, e.g. ``/nhl/schedule/team?date=2023-11-10``.

    Without ``date``, uses today. Returns 404 when the team doesn't play
    that day.
    """
    try:
        game = await service.get_team_game_on_date(
            date, team_abbreviation=team_abbreviation
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx2.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"NHL API error: {exc.response.status_code}",
        ) from exc
    except httpx2.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail=f"NHL API unavailable: {exc}"
        ) from exc
    if game is None:
        raise HTTPException(status_code=404, detail="No team game on that date")
    return game


@router.get(
    "/{date}",
    response_model=NhlScheduleResponse,
    summary="NHL schedule by date",
    responses=_ERROR_RESPONSES,
)
async def get_schedule_by_date(
    date: str = Path(
        description="Schedule date in YYYY-MM-DD format.",
        examples=["2023-11-10"],
    ),
    service: NhlService = Depends(get_nhl_service),  # noqa: B008
) -> NhlScheduleResponse:
    """Get the NHL schedule week containing ``date``, e.g. ``/nhl/schedule/2023-11-10``."""
    try:
        return await service.get_schedule_by_date(date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx2.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"NHL API error: {exc.response.status_code}",
        ) from exc
    except httpx2.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail=f"NHL API unavailable: {exc}"
        ) from exc
