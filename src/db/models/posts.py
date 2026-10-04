from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base

if TYPE_CHECKING:
    from db.models.games import Game


class Post(Base):
    """Highlight clip deduped by natural key (replaces the Dynamo conditional write)."""

    __tablename__ = "posts"

    post_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.game_id"), index=True, nullable=False
    )
    event_id: Mapped[int | None]
    team: Mapped[str] = mapped_column(String(10), nullable=False)
    posted: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    game: Mapped[Game] = relationship("Game", back_populates="posts")
