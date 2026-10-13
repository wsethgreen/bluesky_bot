"""Game-day poll worker: detect today's game, persist it, claim new clips."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.games import Game
from models.nhl import NhlPlay, NhlScheduleGame, NhlScheduleTeam
from repos.games_repo import GamesRepo
from repos.goals_repo import GoalsRepo
from repos.posts_repo import PostsRepo
from repos.teams_repo import TeamsRepo
from services.nhl_service import NhlService

logger = logging.getLogger(__name__)


class PollWorker:
    """One poll cycle for the followed team.

    1. Find today's game via the schedule API.
    2. Upsert both teams (FK targets), the game, and its goals.
    3. Claim unseen highlight clips as unposted ``Post`` rows for the poster.
    """

    def __init__(
        self,
        *,
        nhl: NhlService,
        sessions: async_sessionmaker[AsyncSession],
        team_abbreviation: str,
    ) -> None:
        self._nhl = nhl
        self._sessions = sessions
        self._team = team_abbreviation.strip().upper()

    async def poll_once(self) -> str:
        """Run a single cycle, returning a human-readable summary."""
        if not self._team:
            return "skipped: NHL_TEAM_ABBREVIATION not set"

        game = await self._nhl.get_team_game_on_date(team_abbreviation=self._team)
        if game is None:
            return f"no {self._team} game today"
        if game.id is None:
            return f"game found but has no id ({game.game_state})"
        if (
            game.away_team is None
            or game.away_team.id is None
            or game.home_team is None
            or game.home_team.id is None
        ):
            return f"game {game.id} missing team ids (not stored)"

        goals = await self._nhl.get_team_goals_on_date(team_abbreviation=self._team)
        new_clips = await self._persist(game, goals)

        away = game.away_team.abbrev if game.away_team else "?"
        home = game.home_team.abbrev if game.home_team else "?"
        summary = (
            f"{away} @ {home} ({game.game_state}): "
            f"{len(goals)} goals, {new_clips} new clips"
        )
        logger.info("poll: %s", summary)
        return summary

    async def _persist(self, game: NhlScheduleGame, goals: list[NhlPlay]) -> int:
        """Upsert teams/game/goals, claim unseen clips. Returns new clip count."""
        assert game.id is not None
        async with self._sessions() as session:
            teams = TeamsRepo(session)
            games = GamesRepo(session)
            goals_repo = GoalsRepo(session)
            posts = PostsRepo(session)
            for side in (game.away_team, game.home_team):
                await self._upsert_team(teams, side)
            await games.upsert_game(self._build_game(game))
            clips = await self._upsert_goals(goals_repo, game.id, goals)
            new_clips = await self._claim_clips(posts, game.id, clips)
            await session.commit()
            return new_clips

    async def _upsert_team(
        self, teams: TeamsRepo, side: NhlScheduleTeam | None
    ) -> None:
        if side is None or side.id is None:
            return
        stats = await self._nhl.get_team(side.id)
        await teams.upsert_from_stats(stats)

    def _build_game(self, game: NhlScheduleGame) -> Game:
        assert game.id is not None
        assert game.away_team and game.away_team.id is not None
        assert game.home_team and game.home_team.id is not None
        if game.start_time_utc:
            start = datetime.fromisoformat(game.start_time_utc)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
        else:
            start = datetime.now(UTC)
        return Game(
            game_id=game.id,
            season=game.season or 0,
            game_type=game.game_type or 0,
            start_time=start,
            game_state=game.game_state,
            game_schedule_state=game.game_schedule_state,
            away_team_id=game.away_team.id,
            away_team_score=game.away_team.score,
            home_team_id=game.home_team.id,
            home_team_score=game.home_team.score,
        )

    async def _upsert_goals(
        self, goals_repo: GoalsRepo, game_id: int, plays: list[NhlPlay]
    ) -> dict[int, int]:
        """Upsert goal rows. Returns {highlight_clip: event_id}."""
        clips: dict[int, int] = {}
        for play in plays:
            goal = await goals_repo.upsert_from_play(game_id, play)
            if goal is not None and goal.highlight_clip_id is not None:
                clips[goal.highlight_clip_id] = goal.event_id
        return clips

    async def _claim_clips(
        self, posts: PostsRepo, game_id: int, clips: dict[int, int]
    ) -> int:
        """Insert unposted Post rows for unseen clips. Returns new row count."""
        new_clips = 0
        for clip_id, event_id in clips.items():
            if await posts.claim_pending(clip_id, game_id, self._team, event_id):
                new_clips += 1
        return new_clips
