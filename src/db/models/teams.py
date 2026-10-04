from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class Team(Base):
    """NHL team directory (the bot looks up the followed team by tri-code)."""

    __tablename__ = "teams"

    # Natural key: NHL-assigned id, always supplied on insert.
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    franchise_id: Mapped[int] = mapped_column(nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    league_id: Mapped[int] = mapped_column(nullable=False)
    raw_tri_code: Mapped[str] = mapped_column(String(10), nullable=False)
    tri_code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
