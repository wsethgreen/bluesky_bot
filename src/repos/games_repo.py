"""Postgres access for the games table (PK: game_id INTEGER)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.games import Game
from db.models.teams import Team
from utils.date import normalize_date


class GamesRepo:
    """Tracked NHL games.

    Owns no transactions: methods flush but never commit, so callers
    (workers, routes) control commit/rollback boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_game(self, game: Game) -> Game:
        """Insert or update a game row. Returns the managed instance."""
        return await self.session.merge(game)

    async def get_game(self, game_id: int) -> Game | None:
        return await self.session.get(Game, game_id)

    async def get_game_by_date(
        self,
        day: str | dt.date,
        team_abbrev: str,
        tz: str = "America/Toronto",
    ) -> Game | None:
        """The team's game on a calendar day in ``tz`` (defaults to app tz).

        Bounds ``start_time`` to the local day instead of truncating the
        timestamp in the DB, so late-UTC games land on the correct local date.
        """
        day_iso = normalize_date(day)
        day_start = dt.datetime.combine(
            dt.date.fromisoformat(day_iso), dt.time.min, tzinfo=ZoneInfo(tz)
        )
        team = team_abbrev.strip().upper()
        result = await self.session.execute(
            select(Game)
            .where(
                Game.start_time >= day_start,
                Game.start_time < day_start + dt.timedelta(days=1),
            )
            .where(
                or_(
                    Game.away_team.has(Team.tri_code == team),
                    Game.home_team.has(Team.tri_code == team),
                )
            )
        )
        return result.scalars().first()
