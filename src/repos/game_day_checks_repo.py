"""Postgres access for the game_day_checks table (PK: team, day)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.game_day_check import GameDayCheck
from utils.date import normalize_date


class GameDayChecksRepo:
    """Cached schedule lookups: "does this team play on this day?".

    Owns no transactions: methods flush but never commit, so callers
    (workers) control commit/rollback boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_check(self, team: str, day: str | dt.date) -> GameDayCheck | None:
        """The cached check for (team, day), or None when never checked."""
        result = await self.session.execute(
            select(GameDayCheck).where(
                GameDayCheck.team == team.strip().upper(),
                GameDayCheck.day == dt.date.fromisoformat(normalize_date(day)),
            )
        )
        return result.scalar_one_or_none()

    async def record_check(
        self, team: str, day: str | dt.date, *, has_game: bool
    ) -> None:
        """Upsert the schedule answer for (team, day).

        Re-checking the same day overwrites the previous answer, so a
        corrected schedule (a game added after an earlier "no game") is
        recorded on the next lookup.
        """
        stmt = (
            pg_insert(GameDayCheck)
            .values(
                team=team.strip().upper(),
                day=dt.date.fromisoformat(normalize_date(day)),
                has_game=has_game,
            )
            .on_conflict_do_update(
                index_elements=["team", "day"],
                set_={"has_game": has_game, "checked_at": func.now()},
            )
        )
        await self.session.execute(stmt)
