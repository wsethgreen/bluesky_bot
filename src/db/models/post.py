from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base

if TYPE_CHECKING:
    from db.models.game import Game


class PostKind:
    """Kinds of Bluesky posts queued in the ``posts`` table.

    Each goal produces up to two rows: a ``text`` row claimed the moment
    the goal is detected, and a ``video`` row claimed once the NHL
    attaches the highlight clip id.
    """

    TEXT = "text"
    VIDEO = "video"


class Post(Base):
    """One pending/posted Bluesky post, deduped by (game, event, kind).

    The PK is a surrogate ``id``; the old clip-id primary key moved to
    the nullable unique ``clip_id`` column (video rows only). A video
    row may quote its sibling text row via the strong ref captured in
    ``posted_uri``/``posted_cid`` when that text post went out.
    """

    __tablename__ = "posts"
    __table_args__ = (
        CheckConstraint("kind in ('text', 'video')", name="posts_kind_check"),
        UniqueConstraint(
            "game_id", "event_id", "kind", name="posts_game_event_kind_key"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.game_id"), index=True, nullable=False
    )
    event_id: Mapped[int | None]
    team: Mapped[str] = mapped_column(String(10), nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    clip_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    highlight_url: Mapped[str | None] = mapped_column(String, nullable=True)
    posted: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false(), index=True
    )
    defunct: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false(), index=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    posted_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    posted_cid: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    game: Mapped[Game] = relationship("Game", back_populates="posts")
