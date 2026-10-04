"""Team routes (NHL Stats API directory entry)."""

from __future__ import annotations

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Path

from models.nhl_stats import NhlStatsTeam
from routes.dependencies import get_nhl_service
from services.nhl_service import NhlService

router = APIRouter(prefix="/nhl/teams", tags=["nhl"])

_ERROR_RESPONSES = {
    422: {"description": "Invalid team id (must be a positive integer)."},
    404: {"description": "No team with that id."},
    502: {"description": "Upstream API returned an error status."},
    503: {"description": "Upstream API unreachable (network/timeout)."},
}


@router.get(
    "/{team_id}",
    response_model=NhlStatsTeam,
    summary="Team directory entry by id",
    responses=_ERROR_RESPONSES,
)
async def get_team(
    team_id: int = Path(
        description="NHL team id, e.g. 29.",
        examples=[29],
        gt=0,
    ),
    service: NhlService = Depends(get_nhl_service),  # noqa: B008
) -> NhlStatsTeam:
    """Get a team's directory entry, e.g. ``/nhl/teams/29``."""
    try:
        team = await service.get_team(team_id)
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
    if team is None:
        raise HTTPException(status_code=404, detail="No team with that id")
    return team
