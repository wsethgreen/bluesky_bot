from pydantic import Field

from models.base import _Base


class NhlStatsTeam(_Base):
    id: int | None = None
    franchise_id: int | None = Field(default=None, alias="franchiseId")
    full_name: str | None = Field(default=None, alias="fullName")
    league_id: int | None = Field(default=None, alias="leagueId")
    raw_tri_code: str | None = Field(default=None, alias="rawTricode")
    tri_code: str | None = Field(default=None, alias="triCode")


class NhlStatsTeamResponse(_Base):
    data: list[NhlStatsTeam] = Field(default_factory=list)
    total: int = 0
