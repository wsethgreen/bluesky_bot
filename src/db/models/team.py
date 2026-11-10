from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class Team(Base):
    """Team directory shared by every league's poller.

    One row per team, keyed by the id its league's feed assigns — the
    NHL stats API's ids, or (for NBA rows) ESPN's ids behind
    :func:`nba_team_row_id`'s offset.
    """

    __tablename__ = "teams"
    # Tri-codes repeat across leagues — ESPN and the NHL both use BOS,
    # CHI, DAL, DET, MTL, PHI, TOR, UTA — so uniqueness is per league,
    # not global (migration d4b6e8f2a1c3).
    __table_args__ = (
        UniqueConstraint("league", "tri_code", name="uq_teams_league_tri_code"),
    )

    # Natural key: id assigned by the row's league feed, always supplied
    # on insert. NBA rows are offset (see nba_team_row_id) so ESPN's
    # small ids can't collide with the NHL's as the primary key.
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    # Owning league ("nhl" / "nba"). No DB default: every insert must
    # state its league explicitly (mirrors games.league).
    league: Mapped[str] = mapped_column(String(10), nullable=False)
    franchise_id: Mapped[int] = mapped_column(nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    league_id: Mapped[int] = mapped_column(nullable=False)
    raw_tri_code: Mapped[str] = mapped_column(String(10), nullable=False)
    tri_code: Mapped[str] = mapped_column(String(10), nullable=False)


# NBA team rows live behind this offset: ESPN numbers its teams 1..30,
# the same range the NHL stats API uses, so storing ESPN's raw id as the
# primary key would collide with (and session.merge would silently
# overwrite) an existing NHL team. The offset keeps ids inside INTEGER
# (< 2^31) while ESPN's ids stay under ~147M — 30 teams have no risk.
NBA_TEAM_ID_OFFSET = 2_000_000_000


def nba_team_row_id(espn_team_id: int) -> int:
    """``teams`` primary key for an NBA team's ESPN id.

    Raises ``ValueError`` on an id that is already an NBA-row id (or
    nonsense), so a double-offset can never hide behind another lookup.
    """
    if not 0 < espn_team_id < NBA_TEAM_ID_OFFSET:
        raise ValueError(
            f"invalid ESPN team id {espn_team_id!r} "
            f"(expected 0 < id < {NBA_TEAM_ID_OFFSET})"
        )
    return NBA_TEAM_ID_OFFSET + espn_team_id
