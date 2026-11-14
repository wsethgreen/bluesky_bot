"""Team video routes (NBA content API: team-site video feed).

The feed carries the videos pickable for posting — game highlights,
pre/post-game pressers (media availability), and other gameday clips —
as plain, DRM-free MP4s in ``videoAssets``.
"""

from __future__ import annotations

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Query

from models.nba_content import NbaContentVideo
from routes.dependencies import get_nba_service
from services.nba_service import NbaService

router = APIRouter(prefix="/nba/videos", tags=["nba"])

_ERROR_RESPONSES = {
    422: {"description": "Invalid count/offset (or other invalid argument)."},
    502: {"description": "Upstream API returned an error status."},
    503: {"description": "Upstream API unreachable (network/timeout)."},
}


@router.get(
    "",
    response_model=list[NbaContentVideo],
    summary="Team's postable videos (highlights, pre/post game, media)",
    responses=_ERROR_RESPONSES,
)
async def list_team_videos(
    category: str | None = Query(
        default=None,
        description=(
            "Taxonomy slug filter passed upstream as ``term-category``. "
            "Common values: ``game-highlights``, ``gameday`` (all game-day "
            "clips incl. pre/post-game), ``media-availability`` (pressers). "
            "Unset returns every team video."
        ),
        examples=["game-highlights"],
    ),
    count: int = Query(
        default=50,
        ge=1,
        le=100,
        description="Page size (items), newest first.",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Items to skip (for paging past the first page).",
    ),
    service: NbaService = Depends(get_nba_service),  # noqa: B008
) -> list[NbaContentVideo]:
    """Get the team's site videos, e.g. ``/nba/videos?category=game-highlights``.

    Newest first; each item carries its ``videoAssets`` renditions (MP4
    URLs), thumbnail, duration, and taxonomy so you can pick what to post.
    Omit ``category`` to browse everything available.
    """
    try:
        # HTTPException (404 etc.) passes through this mapping untouched.
        return await service.get_team_videos(
            count=count, offset=offset, category=category
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
