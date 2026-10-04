from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class Player(Base):
    """Persisted player snapshot (flattened landing payload, not a cache)."""

    __tablename__ = "players"

    # Natural key: NHL-assigned id, always supplied on insert.
    player_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    team_full_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    current_team_id: Mapped[int | None] = mapped_column(index=True)
    current_team_abbrev: Mapped[str | None] = mapped_column(String(10), index=True)
    sweater_number: Mapped[int | None]
    position: Mapped[str | None] = mapped_column(String(5))
    headshot: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool | None] = mapped_column(Boolean)
    season: Mapped[int | None]
    games_played: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    goals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assists: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plus_minus: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    power_play_goals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    power_play_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shorthanded_goals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shorthanded_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
