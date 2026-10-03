"""Team-focused operations over the official NHL Web API."""

from __future__ import annotations

import asyncio
import datetime
from zoneinfo import ZoneInfo

from clients.brightcove_client import BrightcoveClient
from clients.nhl_client import NhlClient
from config.settings import get_settings
from models.brightcove import BrightcoveVideo
from models.nhl import NhlPlay, NhlScheduleGame, NhlScheduleResponse
from utils.date import normalize_date


class NhlService:
    """Schedule lookup scoped to the configured team."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = NhlClient(base_url=self.settings.nhl_web_api_base_url)
        self._brightcove: BrightcoveClient | None = None

    @property
    def brightcove(self) -> BrightcoveClient:
        """Brightcove client, built on first use so the service stays
        constructible without a policy key until video is actually needed."""
        if self._brightcove is None:
            self._brightcove = BrightcoveClient()
        return self._brightcove

    def _today(self) -> str:
        return (
            datetime.datetime.now(ZoneInfo(self.settings.app_timezone))
            .date()
            .isoformat()
        )

    async def get_schedule_by_date(
        self, date: str | datetime.date | None = None
    ) -> NhlScheduleResponse:
        """Fetch the schedule week containing ``date`` (defaults to today)."""
        return await self.client.get_schedule(
            normalize_date(date) if date is not None else self._today()
        )

    async def get_team_game_on_date(
        self,
        date: str | datetime.date | None = None,
        team_abbreviation: str | None = None,
    ) -> NhlScheduleGame | None:
        """The configured team's game on ``date`` (defaults to today).

        Searches only the requested day within the returned week — a game
        later in the week is not "today's game". Returns ``None`` when the
        team doesn't play that day.
        """
        team = (team_abbreviation or self.settings.nhl_team_abbreviation).strip()
        if not team:
            raise ValueError(
                "team abbreviation is required "
                "(pass team_abbreviation or set NHL_TEAM_ABBREVIATION)"
            )
        day = normalize_date(date) if date is not None else self._today()
        schedule = await self.client.get_schedule(day)
        for schedule_day in schedule.game_week:
            if schedule_day.date != day:
                continue
            return next((g for g in schedule_day.games if g.involves(team)), None)
        return None

    async def get_goals_for_game(self, game_id: int) -> list[NhlPlay]:
        """Goal plays for a game, in sequence.

        Fetches all plays via :meth:`NhlClient.get_plays` and keeps only
        ``typeDescKey == "goal"`` events.
        """
        plays = await self.client.get_plays(game_id)
        return [p for p in plays if p.type_desc_key == "goal"]

    async def get_team_goals_on_date(
        self,
        date: str | datetime.date | None = None,
        *,
        team_abbreviation: str | None = None,
    ) -> list[NhlPlay]:
        """Goal plays scored *by* the team on ``date`` (defaults to today).

        Finds the team's game, resolves the team's numeric id from the
        matchup, and keeps only goals with a matching ``eventOwnerTeamId``.
        Empty list when the team doesn't play, hasn't scored, or a goal
        can't be attributed to a side.
        """
        game = await self.get_team_game_on_date(
            date, team_abbreviation=team_abbreviation
        )
        if game is None or game.id is None:
            return []
        team = (
            (team_abbreviation or self.settings.nhl_team_abbreviation).strip().upper()
        )
        team_id: int | None = None
        for side in (game.home_team, game.away_team):
            if side is not None and (side.abbrev or "").upper() == team:
                team_id = side.id
                break
        if team_id is None:
            return []
        goals = await self.get_goals_for_game(game.id)
        return [
            goal
            for goal in goals
            if goal.details is not None and goal.details.event_owner_team_id == team_id
        ]

    async def get_goal_video(self, goal: NhlPlay) -> BrightcoveVideo | None:
        """Brightcove video for a goal play, via its ``highlightClip`` id.

        Returns ``None`` when the play carries no clip id (not every goal
        has one) instead of failing the whole pipeline.
        """
        clip_id = goal.details.highlight_clip if goal.details else None
        if clip_id is None:
            return None
        return await self.brightcove.get_video(clip_id)

    async def get_goal_videos_for_goals(
        self, goals: list[NhlPlay]
    ) -> list[BrightcoveVideo]:
        """Resolve video objects for goal plays, skipping clipless goals."""
        videos = await asyncio.gather(*(self.get_goal_video(goal) for goal in goals))
        return [video for video in videos if video is not None]

    async def get_goal_videos_for_team_on_date(
        self,
        date: str | datetime.date | None = None,
        *,
        team_abbreviation: str | None = None,
    ) -> list[BrightcoveVideo]:
        """Video objects for the team's goals on ``date`` (defaults to today).

        Full pipeline in one call: find the game, take its goals, resolve
        each goal's clip. Empty list when the team doesn't play that day
        or no clips exist yet.
        """
        game = await self.get_team_game_on_date(
            date, team_abbreviation=team_abbreviation
        )
        if game is None or game.id is None:
            return []
        goals = await self.get_goals_for_game(game.id)
        return await self.get_goal_videos_for_goals(goals)
