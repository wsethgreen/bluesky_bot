from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class GameDayCheck(Base):
    """Cached answer to "does <team> play on <day>?".

    The poller needs to tell two states apart when ``games`` has no row
    for today: "no game" (skip) versus "game not yet discovered" (go
    fetch). The games table alone cannot distinguish them, so a negative
    schedule lookup is recorded here.

    Keyed by (team, day), so the cache expires naturally at midnight —
    no TTL logic required.

    Only negative results are stored: a positive lookup produces a
    ``games`` row, which is the authoritative "there is a game" record
    and which the poller retries until it is fully written.
    """

    __tablename__ = "game_day_checks"

    team: Mapped[str] = mapped_column(String(10), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    has_game: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
