"""Game-day poll worker: detect today's game, persist it, claim new clips."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.games import Game
from db.models.goals import Goal
from db.models.posts import Post
from db.models.teams import Team
from models.nhl import NhlPlay, NhlScheduleGame, NhlScheduleTeam
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
            for side in (game.away_team, game.home_team):
                await self._upsert_team(session, side)
            await session.merge(self._build_game(game))
            clips = await self._upsert_goals(session, game.id, goals)
            new_clips = await self._claim_clips(session, game.id, clips)
            await session.commit()
            return new_clips

    async def _upsert_team(
        self, session: AsyncSession, side: NhlScheduleTeam | None
    ) -> None:
        if side is None or side.id is None:
            return
        stats = await self._nhl.get_team(side.id)
        if stats is None or stats.id is None:
            return
        await session.merge(
            Team(
                id=stats.id,
                franchise_id=stats.franchise_id or 0,
                full_name=stats.full_name or "",
                league_id=stats.league_id or 0,
                raw_tri_code=stats.raw_tri_code or "",
                tri_code=stats.tri_code or "",
            )
        )

    def _build_game(self, game: NhlScheduleGame) -> Game:
        assert game.id is not None
        assert game.away_team and game.away_team.id is not None
        assert game.home_team and game.home_team.id is not None
        start = (
            datetime.fromisoformat(game.start_time_utc)
            if game.start_time_utc
            else datetime.now().astimezone()
        )
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
        self, session: AsyncSession, game_id: int, plays: list[NhlPlay]
    ) -> dict[int, int]:
        """Merge goal rows. Returns {highlight_clip: event_id}."""
        clips: dict[int, int] = {}
        for play in plays:
            details = play.details
            if details is None or play.event_id is None:
                continue
            await session.merge(
                Goal(
                    game_id=game_id,
                    event_id=play.event_id,
                    period_number=play.period.number if play.period else 0,
                    time_in_period=play.time_in_period or "",
                    time_remaining=play.time_remaining or "",
                    scoring_player_id=details.scoring_player_id,
                    assist1_player_id=details.assist1_player_id,
                    assist2_player_id=details.assist2_player_id,
                    away_score=details.away_score or 0,
                    home_score=details.home_score or 0,
                    highlight_clip_id=details.highlight_clip,
                )
            )
            if details.highlight_clip is not None:
                clips[details.highlight_clip] = play.event_id
        return clips

    async def _claim_clips(
        self, session: AsyncSession, game_id: int, clips: dict[int, int]
    ) -> int:
        """Insert unposted Post rows for unseen clips. Returns new row count."""
        if not clips:
            return 0
        seen = set(
            (await session.execute(select(Post.post_id).where(Post.post_id.in_(clips))))
            .scalars()
            .all()
        )
        fresh = {clip: event for clip, event in clips.items() if clip not in seen}
        for clip, event_id in fresh.items():
            session.add(
                Post(
                    post_id=clip,
                    game_id=game_id,
                    event_id=event_id,
                    team=self._team,
                    posted=False,
                )
            )
        return len(fresh)
