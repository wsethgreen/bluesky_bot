"""Team-focused operations over the official NHL Web API."""

from __future__ import annotations

import asyncio
import datetime
from zoneinfo import ZoneInfo

from clients.brightcove_client import BrightcoveClient
from clients.nhl_client import NhlClient
from clients.nhl_stats_client import NhlStatsClient
from config.settings import get_settings
from models.brightcove import BrightcoveVideo
from models.nhl import (
    NhlLiveSnapshot,
    NhlPenalty,
    NhlPlay,
    NhlPlayByPlay,
    NhlPlayByPlayTeam,
    NhlScheduleGame,
    NhlScheduleResponse,
    NhlTeamTotals,
)
from models.nhl_stats import NhlStatsTeam
from services.nhl_game_translator import NhlGameTranslator
from utils.date import normalize_date


class NhlService:
    """Schedule lookup scoped to the configured team.

    Owns the API clients plus an :class:`NhlGameTranslator` — the
    semantic layer that turns feed vocabulary (situation codes, event
    keys) into domain concepts. The translator is constructor-injected
    (defaults to a fresh instance) so tests and future callers can
    swap it.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = NhlClient(base_url=self.settings.nhl.web_api_base_url)
        self.stats = NhlStatsClient(base_url=self.settings.nhl.stats_api_base_url)
        self.game_translator = NhlGameTranslator()
        self._brightcove: BrightcoveClient | None = None

    @property
    def brightcove(self) -> BrightcoveClient:
        """Brightcove client, built on first use so the service stays
        constructible without a policy key until video is actually needed."""
        if self._brightcove is None:
            self._brightcove = BrightcoveClient()
        return self._brightcove

    @property
    def today(self):
        return self._today()

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
        team = (team_abbreviation or self.settings.nhl.team_abbreviation).strip()
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

    @staticmethod
    def is_shootout_play(play: NhlPlay) -> bool:
        """Whether a play took place in the shootout (period type ``SO``).

        The feed runs the shootout as period 5 with ``periodType: "SO"``
        and tags its attempts — even the ones recorded as ``goal`` plays —
        with that descriptor.
        """
        return play.period is not None and play.period.period_type == "SO"

    @staticmethod
    def is_scored_goal(play: NhlPlay) -> bool:
        """A real (regulation/overtime) goal, never a shootout attempt.

        Shootout attempts carry ``typeDescKey == "goal"`` too, but they
        don't change the score, don't count toward player totals, and
        their ``situationCode`` (one shooter, no five-a-side setup)
        misparses as a 1-on-0 "power play" — so every goal-scoring path
        drops them here.
        """
        return play.type_desc_key == "goal" and not NhlService.is_shootout_play(play)

    async def get_goals_for_game(self, game_id: int) -> list[NhlPlay]:
        """Real goal plays for a game, in sequence (no shootout attempts).

        Fetches all plays via :meth:`NhlClient.get_plays` and keeps only
        ``typeDescKey == "goal"`` events outside the shootout — see
        :meth:`is_scored_goal`.
        """
        plays = await self.client.get_plays(game_id)
        return [p for p in plays if self.is_scored_goal(p)]

    async def get_live_snapshot(self, game_id: int, team_id: int) -> NhlLiveSnapshot:
        """One play-by-play fetch: state, our goals, period ends, scoreboard.

        The worker's steady-state path — a single request replaces the
        schedule lookup (game identity comes from the DB row) and the
        separate state refresh. The score/SOG per side, the
        ``period-end`` plays, and every ``penalty`` play all come from
        that same payload: the header counts are the NHL's official
        numbers (a goal counts as a shot on goal there, exactly like
        the SOG column on the scoreboard), so no local event tally is
        needed.

        Each penalty's resulting strength is read from the next
        faceoff after the call (see :meth:`_penalties`). Shootout
        attempts are not goals — :meth:`is_scored_goal` drops them —
        while the shootout's ``period-end`` play stays in
        ``period_ends`` so the final post can claim it.
        """
        pbp = await self.client.get_play_by_play(game_id)
        goals = [
            p
            for p in pbp.plays
            if self.is_scored_goal(p)
            and p.details is not None
            and p.details.event_owner_team_id == team_id
        ]
        period_ends = [p for p in pbp.plays if p.type_desc_key == "period-end"]
        penalties = self._penalties(pbp)
        return NhlLiveSnapshot(
            game_state=pbp.game_state,
            goals=goals,
            period_ends=period_ends,
            penalties=penalties,
            home=self._team_totals(pbp.home_team),
            away=self._team_totals(pbp.away_team),
        )

    def _penalties(self, pbp: NhlPlayByPlay) -> list[NhlPenalty]:
        """Every ``penalty`` play with its resulting strength, in order.

        Skips ``delayed-penalty`` plays — they carry no infraction or
        offender (the real ``penalty`` play follows at the whistle).
        Delegates all situation-code interpretation to the injected
        translator.
        """
        plays = pbp.plays
        home_id = pbp.home_team.id if pbp.home_team else None
        away_id = pbp.away_team.id if pbp.away_team else None
        penalties: list[NhlPenalty] = []
        for index, play in enumerate(plays):
            if play.type_desc_key != "penalty":
                continue
            details = play.details
            # Resulting strength: the next faceoff after the call —
            # the feed settles offsetting/coincidental minors there.
            # Fall back to simulating from the penalty's own code when
            # the poll caught the call as the newest play.
            strength = None
            for later in plays[index + 1 :]:
                if later.type_desc_key == "faceoff":
                    strength = self.game_translator.parse_situation_code(
                        later.situation_code
                    )
                    break
            if strength is None and details is not None:
                owner = details.event_owner_team_id
                # Only simulate when the offender is known to be home;
                # an unknown or away offender with an unparseable
                # faceoff leaves strength None (post omits the line).
                if owner is not None and owner == home_id:
                    strength = self.game_translator.simulate_after_penalty(
                        play.situation_code, offending_is_home=True
                    )
                elif owner is not None and away_id is not None and owner == away_id:
                    strength = self.game_translator.simulate_after_penalty(
                        play.situation_code, offending_is_home=False
                    )
            penalties.append(
                NhlPenalty(
                    play=play,
                    infraction=details.desc_key if details else None,
                    minutes=details.duration if details else None,
                    penalty_type=details.penalty_type if details else None,
                    offending_team_id=details.event_owner_team_id if details else None,
                    committed_by_player_id=(
                        details.committed_by_player_id if details else None
                    ),
                    strength=strength,
                )
            )
        return penalties

    @staticmethod
    def _team_totals(team: NhlPlayByPlayTeam | None) -> NhlTeamTotals:
        """Header counts for one side; empty totals when the side is absent."""
        if team is None:
            return NhlTeamTotals()
        return NhlTeamTotals(
            team_id=team.id,
            abbrev=team.abbrev,
            score=team.score,
            sog=team.sog,
        )

    async def get_player(self, player_id: int) -> dict:
        """Player landing payload as a raw dict.

        Passthrough to :meth:`NhlClient.get_player` — cache it with
        ``PlayersRepo`` at the call site rather than here so the service
        stays free of DynamoDB.
        """
        return await self.client.get_player(player_id)

    async def get_team(self, team_id: int) -> NhlStatsTeam | None:
        """Team directory entry by numeric id.

        Passthrough to :meth:`NhlStatsClient.get_team` — ``None`` when the
        Stats API returns no rows.
        """
        return await self.stats.get_team(team_id)

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
            (team_abbreviation or self.settings.nhl.team_abbreviation).strip().upper()
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
