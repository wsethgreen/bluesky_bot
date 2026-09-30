from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class HighlightlyTeam(_Lenient):
    id: int | None = None
    logo: str | None = None
    name: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    abbreviation: str | None = None


class HighlightlyScore(_Lenient):
    current: str | None = None
    first_period: str | None = Field(default=None, alias="firstPeriod")
    second_period: str | None = Field(default=None, alias="secondPeriod")
    third_period: str | None = Field(default=None, alias="thirdPeriod")
    overtime_period: str | None = Field(default=None, alias="overtimePeriod")


class HighlightlyMatchState(_Lenient):
    period: int | None = None
    clock: int | None = None
    description: str | None = None
    score: HighlightlyScore | None = None
    report: str | None = None


class HighlightlyMatch(_Lenient):
    id: int | None = None
    round: str | None = None
    date: str | None = None
    league: str | None = None
    season: int | None = None
    home_team: HighlightlyTeam | None = Field(default=None, alias="homeTeam")
    away_team: HighlightlyTeam | None = Field(default=None, alias="awayTeam")
    state: HighlightlyMatchState | None = None


class Highlight(_Lenient):
    """Single video highlight. Unknown API fields are ignored."""

    id: int | None = None
    type: Literal["VERIFIED", "UNVERIFIED"] | str | None = None
    title: str | None = None
    description: str | None = None
    img_url: str | None = Field(default=None, alias="imgUrl")
    url: str | None = None
    embed_url: str | None = Field(default=None, alias="embedUrl")
    channel: str | None = None
    source: str | None = None
    category: str | None = None
    match: HighlightlyMatch | None = None


class HighlightlyPagination(_Lenient):
    total_count: int = Field(default=0, alias="totalCount")
    offset: int = 0
    limit: int = 0


class HighlightlyPlan(_Lenient):
    tier: str | None = None
    message: str | None = None


class HighlightlyHighlightsResponse(_Lenient):
    data: list[Highlight] = Field(default_factory=list)
    pagination: HighlightlyPagination | None = None
    plan: HighlightlyPlan | None = None
