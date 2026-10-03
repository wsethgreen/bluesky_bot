"""Player routes (NHL API landing payload, raw JSON)."""

from __future__ import annotations

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Path

from routes.dependencies import get_nhl_service
from services.nhl_service import NhlService

router = APIRouter(prefix="/nhl/players", tags=["nhl"])

_ERROR_RESPONSES = {
    422: {"description": "Invalid player id (must be a positive integer)."},
    502: {"description": "Upstream API returned an error status."},
    503: {"description": "Upstream API unreachable (network/timeout)."},
}


@router.get(
    "/{player_id}",
    summary="Player landing payload by id",
    responses=_ERROR_RESPONSES,
)
async def get_player(
    player_id: int = Path(
        description="NHL player id, e.g. 8478402.",
        examples=[8478402],
        gt=0,
    ),
    service: NhlService = Depends(get_nhl_service),  # noqa: B008
) -> dict:
    """Get a player's landing payload, e.g. ``/nhl/players/8478402``.

    Raw dict (bio, vitals, career totals) — same shape cached in the
    players table.
    """
    try:
        return await service.get_player(player_id)
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
