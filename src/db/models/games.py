from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base

if TYPE_CHECKING:
    from db.models.goals import Goal
    from db.models.posts import Post
    from db.models.teams import Team


class Game(Base):
    __tablename__ = "games"

    # Natural key: NHL-assigned id, always supplied on insert.
    game_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    season: Mapped[int] = mapped_column(nullable=False)
    game_type: Mapped[int] = mapped_column(nullable=False)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    game_state: Mapped[str | None] = mapped_column(String(20))
    game_schedule_state: Mapped[str | None] = mapped_column(String(10))
    away_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id"), nullable=False, index=True
    )
    away_team_score: Mapped[int | None]
    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id"), nullable=False, index=True
    )
    home_team_score: Mapped[int | None]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    away_team: Mapped[Team] = relationship("Team", foreign_keys=[away_team_id])
    home_team: Mapped[Team] = relationship("Team", foreign_keys=[home_team_id])
    posts: Mapped[list[Post]] = relationship("Post", back_populates="game")
    goals: Mapped[list[Goal]] = relationship("Goal", back_populates="game")
