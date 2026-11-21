"""Postgres access for the games table (PK: game_id INTEGER)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.game import Game
from db.models.team import Team
from utils.date import normalize_date


class GamesRepo:
    """Tracked games, one league per row (see ``Game.league``).

    Owns no transactions: methods flush but never commit, so callers
    (workers, routes) control commit/rollback boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_game(self, game: Game) -> Game:
        """Insert or update a game row. Returns the managed instance.

        Tracking columns (``start_schedule_id``, ``poll_schedule_id``,
        ``ended_at``) are owned by the scheduler and the poller, not by
        the league payloads that build a fresh ``Game``, so they are
        carried over from the stored row rather than cleared.
        """
        existing = await self.session.get(Game, game.game_id)
        if existing is not None:
            game.start_schedule_id = existing.start_schedule_id
            game.poll_schedule_id = existing.poll_schedule_id
            game.ended_at = existing.ended_at
        return await self.session.merge(game)

    async def get_game(self, game_id: int) -> Game | None:
        return await self.session.get(Game, game_id)

    async def get_game_by_date(
        self,
        day: str | dt.date,
        team_abbrev: str,
        *,
        league: str,
        tz: str = "America/Toronto",
    ) -> Game | None:
        """The team's game on a calendar day in ``tz`` (defaults to app tz).

        Bounds ``start_time`` to the local day instead of truncating the
        timestamp in the DB, so late-UTC games land on the correct local date.

        ``league`` scopes the match: tri-codes repeat across leagues
        (both Boston teams are ``BOS``), so without it the other
        league's game could come back as the tracked team's.
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
            .where(Game.league == league)
            .where(
                or_(
                    Game.away_team.has(Team.tri_code == team),
                    Game.home_team.has(Team.tri_code == team),
                )
            )
        )
        return result.scalars().first()

    async def get_game_for_update(self, game_id: int) -> Game | None:
        """Load the game and hold a row lock until the session commits or rolls back."""
        result = await self.session.execute(
            select(Game).where(Game.game_id == game_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def set_start_schedule_id(
        self, game_id: int, schedule_id: str | None
    ) -> bool:
        """Record the schedule that launches tracking. Pass None to clear it."""
        result = await self.session.execute(
            update(Game)
            .where(Game.game_id == game_id)
            .values(start_schedule_id=schedule_id)
        )
        return result.rowcount > 0

    async def set_poll_schedule_id(self, game_id: int, schedule_id: str | None) -> bool:
        """Record the recurring poll schedule. Pass None to clear it after unscheduling."""
        result = await self.session.execute(
            update(Game)
            .where(Game.game_id == game_id)
            .values(poll_schedule_id=schedule_id)
        )
        return result.rowcount > 0
