"""Team-focused operations over the NBA scoreboard (ESPN site API)."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from clients.nba_live_client import NbaLiveClient
from config.settings import get_settings
from models.nba_live import NbaScheduleGame, NbaScoreboardResponse
from utils.date import normalize_date


class NbaService:
    """Scoreboard lookup scoped to the configured team.

    The NBA counterpart of :class:`NhlService`: owns the live client plus
    the app-timezone ``today`` that drives game-day caching. ESPN's
    scoreboard day is Eastern, which matches the default app timezone
    (America/Toronto), so ``today`` and the feed's ``dates`` window
    always describe the same set of games.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = NbaLiveClient(base_url=self.settings.nba.site_api_base_url)

    @property
    def today(self) -> str:
        return (
            datetime.datetime.now(ZoneInfo(self.settings.app_timezone))
            .date()
            .isoformat()
        )

    async def get_scoreboard(
        self, day: str | datetime.date | None = None
    ) -> NbaScoreboardResponse:
        """The scoreboard for ``day`` (defaults to today in the app tz)."""
        return await self.client.get_scoreboard(
            normalize_date(day) if day is not None else self.today
        )

    async def get_team_game_on_date(
        self,
        date: str | datetime.date | None = None,
        team_abbreviation: str | None = None,
    ) -> NbaScheduleGame | None:
        """The configured team's game on ``date`` (defaults to today).

        Returns ``None`` when the team doesn't play that day.
        """
        team = (team_abbreviation or self.settings.nba.team_abbreviation).strip()
        if not team:
            raise ValueError(
                "team abbreviation is required "
                "(pass team_abbreviation or set NBA_TEAM_ABBREVIATION)"
            )
        day = normalize_date(date) if date is not None else self.today
        scoreboard = await self.get_scoreboard(day)
        return next((g for g in scoreboard.events if g.involves(team)), None)
