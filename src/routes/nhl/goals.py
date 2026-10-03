"""Team goal routes (NHL API schedule + play-by-play, metadata only)."""

from __future__ import annotations

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Query

from models.nhl import NhlPlay
from routes.dependencies import get_nhl_service
from services.nhl_service import NhlService

router = APIRouter(prefix="/nhl/goals", tags=["nhl"])

_ERROR_RESPONSES = {
    422: {"description": "Invalid date format (expected YYYY-MM-DD)."},
    502: {"description": "Upstream API returned an error status."},
    503: {"description": "Upstream API unreachable (network/timeout)."},
}


@router.get(
    "",
    response_model=list[NhlPlay],
    summary="Team's goals on a date",
    responses=_ERROR_RESPONSES,
)
async def list_team_goals(
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
) -> list[NhlPlay]:
    """Get goals scored by the team on ``date``, e.g. ``/nhl/goals?date=2023-11-10``.

    Both params optional: unset ``date`` means today, unset
    ``team_abbreviation`` means the configured team. Empty list when the
    team doesn't play that day or hasn't scored. Metadata only — for video
    files see ``/nhl/videos``.
    """
    try:
        return await service.get_team_goals_on_date(
            date, team_abbreviation=team_abbreviation
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx2.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream API error: {exc.response.status_code}",
        ) from exc
    except httpx2.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail=f"Upstream API unavailable: {exc}"
        ) from exc
