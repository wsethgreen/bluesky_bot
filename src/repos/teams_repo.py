"""Postgres access for the teams table (PK: id INTEGER)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.team import Team, nba_team_row_id
from models.nhl_stats import NhlStatsTeam


class TeamsRepo:
    """Team directory, one row per league team (see ``Team.league``).

    Owns no transactions: methods flush but never commit, so callers
    (workers, routes) control commit/rollback boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_team(self, team_id: int) -> Team | None:
        return await self.session.get(Team, team_id)

    async def get_by_tri_code(self, tri_code: str, *, league: str) -> Team | None:
        """Team by tri-code within ``league`` (e.g. ``league="nhl"``, ``"CBJ"``).

        ``league`` is required: codes repeat across leagues (both
        Boston teams are ``BOS``), so a cross-league match must never be
        returned as the team the caller means.
        """
        result = await self.session.execute(
            select(Team).where(
                Team.tri_code == tri_code.strip().upper(),
                Team.league == league,
            )
        )
        return result.scalars().first()

    async def upsert_team(self, team: Team) -> Team:
        """Insert or update a team row. Returns the managed instance."""
        return await self.session.merge(team)

    async def upsert_from_stats(self, stats: NhlStatsTeam | None) -> Team | None:
        """Build an NHL row from a Stats API entry and upsert it.

        Returns ``None`` when there is nothing to store (missing entry/id).
        """
        if stats is None or stats.id is None:
            return None
        return await self.upsert_team(
            Team(
                id=stats.id,
                league="nhl",
                franchise_id=stats.franchise_id or 0,
                full_name=stats.full_name or "",
                league_id=stats.league_id or 0,
                raw_tri_code=stats.raw_tri_code or "",
                tri_code=stats.tri_code or "",
            )
        )

    async def upsert_from_nba(
        self, *, team_id: int, display_name: str, tri_code: str
    ) -> Team:
        """Build an NBA row from an ESPN scoreboard team entry and upsert it.

        ``team_id`` is ESPN's raw team id, namespaced into the primary
        key via :func:`nba_team_row_id`. The Stats-API-only columns
        (franchise/league ids) store 0 — ESPN's payload has no
        equivalent and nothing reads them back.
        """
        return await self.upsert_team(
            Team(
                id=nba_team_row_id(team_id),
                league="nba",
                franchise_id=0,
                full_name=display_name or "",
                league_id=0,
                raw_tri_code=tri_code,
                tri_code=tri_code,
            )
        )
