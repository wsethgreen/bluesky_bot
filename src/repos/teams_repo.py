"""Postgres access for the teams table (PK: id INTEGER)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.team import Team
from models.nhl_stats import NhlStatsTeam


class TeamsRepo:
    """NHL team directory.

    Owns no transactions: methods flush but never commit, so callers
    (workers, routes) control commit/rollback boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_team(self, team_id: int) -> Team | None:
        return await self.session.get(Team, team_id)

    async def get_by_tri_code(self, tri_code: str) -> Team | None:
        """Team by tri-code (e.g. ``CBJ``), case-insensitive."""
        result = await self.session.execute(
            select(Team).where(Team.tri_code == tri_code.strip().upper())
        )
        return result.scalars().first()

    async def upsert_team(self, team: Team) -> Team:
        """Insert or update a team row. Returns the managed instance."""
        return await self.session.merge(team)

    async def upsert_from_stats(self, stats: NhlStatsTeam | None) -> Team | None:
        """Build a row from a Stats API entry and upsert it.

        Returns ``None`` when there is nothing to store (missing entry/id).
        """
        if stats is None or stats.id is None:
            return None
        return await self.upsert_team(
            Team(
                id=stats.id,
                franchise_id=stats.franchise_id or 0,
                full_name=stats.full_name or "",
                league_id=stats.league_id or 0,
                raw_tri_code=stats.raw_tri_code or "",
                tri_code=stats.tri_code or "",
            )
        )
