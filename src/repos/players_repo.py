"""Postgres access for the players table (PK: player_id INTEGER)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.player import Player
from models.player import NhlPlayer


class PlayersRepo:
    """Persisted player snapshots (flattened landing payloads).

    Owns no transactions: methods flush but never commit, so callers
    control commit/rollback boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_player(self, player_id: int) -> Player | None:
        return await self.session.get(Player, player_id)

    async def upsert_player(self, player: Player) -> Player:
        """Insert or update a snapshot row. Returns the managed instance."""
        return await self.session.merge(player)

    async def upsert_from_summary(self, summary: NhlPlayer) -> Player:
        """Build a snapshot from a validated landing summary and upsert it."""
        if summary.player_id is None:
            raise ValueError("NhlPlayer has no player_id")
        return await self.upsert_player(Player(**summary.model_dump()))
