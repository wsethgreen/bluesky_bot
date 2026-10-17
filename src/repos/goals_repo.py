"""Postgres access for the goals table (PK: game_id + event_id)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.goal import Goal
from models.nhl import NhlPlay


class GoalsRepo:
    """Scored goals, keyed by game + NHL event id.

    Owns no transactions: methods flush but never commit, so callers
    (workers, routes) control commit/rollback boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_goal(self, game_id: int, event_id: int) -> Goal | None:
        return await self.session.get(Goal, (game_id, event_id))

    async def upsert_goal(self, goal: Goal) -> Goal:
        """Insert or update a goal row. Returns the managed instance."""
        return await self.session.merge(goal)

    async def upsert_from_play(self, game_id: int, play: NhlPlay) -> Goal | None:
        """Build a row from a play-by-play event and upsert it.

        Returns ``None`` when the play carries no usable goal data
        (missing details/event id), mirroring the poll worker's skip rule.
        """
        details = play.details
        if details is None or play.event_id is None:
            return None
        return await self.upsert_goal(
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

    async def list_for_game(self, game_id: int) -> list[Goal]:
        """All goals for a game, in event sequence."""
        result = await self.session.execute(
            select(Goal).where(Goal.game_id == game_id).order_by(Goal.event_id)
        )
        return list(result.scalars().all())
