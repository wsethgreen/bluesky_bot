"""Team-focused operations over the NBA scoreboard (ESPN site API)."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from clients.nba_content_client import NbaContentClient
from clients.nba_live_client import NbaLiveClient
from config.settings import get_settings
from db.models import Game
from db.models.team import NBA_TEAM_ID_OFFSET
from models.nba_content import NbaContentVideo
from models.nba_live import (
    NbaGameStatus,
    NbaScheduleGame,
    NbaScoreboardResponse,
    NbaSeason,
)
from models.nba_summary import NbaSummaryResponse
from utils.date import normalize_date


class NbaService:
    """Scoreboard lookup scoped to the configured team.

    The NBA counterpart of :class:`NhlService`: owns the live client plus
    the app-timezone ``today`` that drives game-day caching. ESPN's
    scoreboard day is Eastern, which matches the default app timezone
    (America/Toronto), so ``today`` and the feed's ``dates`` window
    always describe the same set of games.

    Also owns :class:`NbaContentClient` (the team-site video feed), which
    serves the pickable, DRM-free MP4 videos the bot could post.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = NbaLiveClient(base_url=self.settings.nba.site_api_base_url)
        self.content = NbaContentClient()

    @property
    def today(self) -> str:
        return (
            datetime.datetime.now(ZoneInfo(self.settings.app_timezone))
            .date()
            .isoformat()
        )

    async def close(self) -> None:
        await self.client.close()
        await self.content.close()

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

    async def get_summary(self, event_id: int) -> NbaSummaryResponse:
        """The game summary for ``event_id`` (quarter scores + team leaders)."""
        return await self.client.get_summary(event_id)

    async def get_team_videos(
        self,
        *,
        count: int = 50,
        offset: int = 0,
        category: str | None = None,
    ) -> list[NbaContentVideo]:
        """One page of the configured team's site videos, newest first.

        ``category`` is a taxonomy slug passed to the upstream
        ``term-category`` filter (e.g. ``game-highlights``,
        ``gameday``, ``media-availability``); ``None`` returns every
        team video (highlights, pre/post-game pressers, misc).
        """
        page = await self.content.get_latest_videos(
            count=count, offset=offset, term_category=category
        )
        return page.results.items

    @staticmethod
    def season_code(year: int) -> int:
        """NHL-shaped season code from ESPN's season *end* year.

        ESPN labels the 2026-27 season ``2027``; the DB stores NHL-style
        ``20262027`` (start * 10000 + end) so both leagues' ``season``
        columns read the same way.
        """
        return (year - 1) * 10000 + year

    @staticmethod
    def game_type_code(season: NbaSeason | None) -> int:
        """NHL-shaped game type: 1 preseason / 2 regular / 3 postseason.

        Mirrors the NHL stats API's ``gameTypeId`` numbering (2 = regular
        season, see docs/nhl-api.md); falls back to ESPN's own numeric
        ``season.type`` when the readable slug is absent, else 0.
        """
        if season is None:
            return 0
        slug = (season.slug or "").strip().lower()
        if slug == "preseason":
            return 1
        if slug == "regular-season":
            return 2
        if slug == "postseason":
            return 3
        return season.type or 0

    @staticmethod
    def map_game_state(status: NbaGameStatus | None) -> str | None:
        """Stored ``Game.game_state`` for an ESPN status; ``None`` = unmapped.

        The column is shared across leagues, so ESPN's vocabulary maps into
        the states the NHL poller already stores: ``pre`` → FUTURE,
        ``in`` → LIVE, ``post`` → FINAL. A settled-but-unplayed game is
        detected from the human ``detail`` first ("Postponed" / "Canceled"),
        so it can never masquerade as FINAL. An unrecognized status returns
        ``None`` and the caller keeps the stored state rather than
        overwriting it with a guess.
        """
        if status is None or status.type is None:
            return None
        detail = (status.type.detail or status.type.short_detail or "").strip().upper()
        if "POSTPON" in detail:
            return "POSTPONED"
        if "CANCEL" in detail:
            return "CANCELED"
        state = (status.type.state or "").strip().lower()
        if state == "post":
            return "FINAL"
        if state == "in":
            return "LIVE"
        if state == "pre":
            return "FUTURE"
        return None

    @staticmethod
    def nba_team_row_id(espn_team_id: int) -> int:
        """``teams`` primary key for an NBA team's ESPN id.

        Raises ``ValueError`` on an id that is already an NBA-row id (or
        nonsense), so a double-offset can never hide behind another lookup.
        """
        if not 0 < espn_team_id < NBA_TEAM_ID_OFFSET:
            raise ValueError(
                f"invalid ESPN team id {espn_team_id!r} "
                f"(expected 0 < id < {NBA_TEAM_ID_OFFSET})"
            )
        return NBA_TEAM_ID_OFFSET + espn_team_id

    def build_game(self, game: NbaScheduleGame) -> Game:
        if not game.id:
            raise ValueError("Game ID cannot be None")

        home, away = game.home_competitor, game.away_competitor

        if not home or not home.team:
            raise ValueError("Home and away teams cannot be None")
        if not away or not away.team:
            raise ValueError("Away and home teams cannot be None")
        if not home.team.id:
            raise ValueError("Home team ID cannot be None")
        if not away.team.id:
            raise ValueError("Away team ID cannot be None")

        if game.date:
            start = datetime.datetime.fromisoformat(game.date)
            if start.tzinfo is None:
                start = start.replace(tzinfo=datetime.UTC)
        else:
            start = datetime.datetime.now(datetime.UTC)
        return Game(
            game_id=game.id,
            league="nba",
            season=self.season_code(game.season.year)
            if game.season is not None and game.season.year
            else 0,
            game_type=self.game_type_code(game.season),
            start_time=start,
            game_state=self.map_game_state(game.game_status),
            game_schedule_state=None,  # ESPN has no schedule-state word
            away_team_id=self.nba_team_row_id(away.team.id),
            away_team_score=away.score,
            home_team_id=self.nba_team_row_id(home.team.id),
            home_team_score=home.score,
        )
