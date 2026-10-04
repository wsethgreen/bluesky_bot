from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base

if TYPE_CHECKING:
    from db.models.posts import Post


class Game(Base):
    __tablename__ = "games"

    # Natural key: NHL-assigned id, always supplied on insert.
    game_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    team: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    game_state: Mapped[str | None] = mapped_column(String(20))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    posts: Mapped[list[Post]] = relationship("Post", back_populates="game")
