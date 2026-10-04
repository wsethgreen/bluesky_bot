from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class Player(Base):
    """Read-through cache for NHL player payloads (replaces the Dynamo TTL row)."""

    __tablename__ = "players"

    # Natural key: NHL-assigned id, always supplied on insert.
    player_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
