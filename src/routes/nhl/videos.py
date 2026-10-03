"""Team goal-video routes (NHL API schedule/plays + Brightcove files)."""

from __future__ import annotations

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Query

from models.brightcove import BrightcoveVideo
from routes.dependencies import get_nhl_service
from services.nhl_service import NhlService

router = APIRouter(prefix="/nhl/videos", tags=["nhl"])

_ERROR_RESPONSES = {
    422: {"description": "Invalid date format (expected YYYY-MM-DD)."},
    502: {"description": "Upstream API returned an error status."},
    503: {"description": "Upstream API unreachable (network/timeout)."},
}


@router.get(
    "",
    response_model=list[BrightcoveVideo],
    summary="Team's goal videos on a date",
    responses={
        **_ERROR_RESPONSES,
        404: {"description": "The team has no game on that date."},
    },
)
async def list_team_goal_videos(
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
) -> list[BrightcoveVideo]:
    """Get goal videos for the team's game on ``date``, e.g. ``/nhl/videos?date=2023-11-10``.

    Both params optional: unset ``date`` means today, unset
    ``team_abbreviation`` means the configured team. Returns 404 when the
    team doesn't play that day, otherwise the clips (possibly empty when
    nothing has surfaced yet).
    """
    try:
        game = await service.get_team_game_on_date(
            date, team_abbreviation=team_abbreviation
        )
        if game is None or game.id is None:
            # Raised inside try so upstream-error mapping below still applies
            # to the service calls above; HTTPException passes through untouched.
            raise HTTPException(status_code=404, detail="No team game on that date")
        goals = await service.get_goals_for_game(game.id)
        return await service.get_goal_videos_for_goals(goals)
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
