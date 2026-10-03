"""Client for the official NHL Web API (no key required)."""

from __future__ import annotations

import datetime

from clients.http_client import HttpClient
from models.nhl import NhlPlay, NhlPlayByPlay, NhlScheduleResponse
from utils.date import normalize_date

DEFAULT_BASE_URL = "https://api-web.nhle.com"


class NhlClient:
    """Thin wrapper around ``api-web.nhle.com``.

    Builds its own :class:`HttpClient`, which already sets the User-Agent
    the API requires (it 403s requests without one).
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self.http: HttpClient = HttpClient()
        self.base_url = base_url.rstrip("/")

    async def get_schedule(self, date: str | datetime.date) -> NhlScheduleResponse:
        """Fetch the schedule week containing ``date``.

        ``GET /v1/schedule/{YYYY-MM-DD}`` — the response centers a week of
        days on the requested date; use ``game_week`` to find the exact day.
        """
        day = normalize_date(date)
        response = await self.http.get(f"{self.base_url}/v1/schedule/{day}")
        return NhlScheduleResponse.model_validate(response.json())

    async def get_plays(self, game_id: int) -> list[NhlPlay]:
        """All plays for a game.

        ``GET /v1/gamecenter/{game-id}/play-by-play`` — every faceoff,
        shot, hit, penalty, and goal in ``sortOrder`` sequence.
        """
        if game_id <= 0:
            raise ValueError(f"game_id must be positive, got {game_id!r}")
        response = await self.http.get(
            f"{self.base_url}/v1/gamecenter/{game_id}/play-by-play"
        )
        return NhlPlayByPlay.model_validate(response.json()).plays
