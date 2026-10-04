from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base

if TYPE_CHECKING:
    from db.models.games import Game


class Goal(Base):
    """A scored goal, keyed by game + NHL event id.

    Event ids restart every game, so neither column is unique alone;
    together they are the natural key.
    """

    __tablename__ = "goals"

    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.game_id"), primary_key=True, autoincrement=False
    )
    event_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    period_number: Mapped[int] = mapped_column(nullable=False)
    time_in_period: Mapped[str] = mapped_column(String(5), nullable=False)
    time_remaining: Mapped[str] = mapped_column(String(5), nullable=False)
    scoring_player_id: Mapped[int | None] = mapped_column(index=True)
    assist1_player_id: Mapped[int | None]
    assist2_player_id: Mapped[int | None]
    away_score: Mapped[int] = mapped_column(nullable=False)
    home_score: Mapped[int] = mapped_column(nullable=False)
    highlight_clip_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)

    game: Mapped[Game] = relationship("Game", back_populates="goals")
