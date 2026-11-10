"""Models for the NBA game summary feed (ESPN site API).

``GET /apis/site/v2/sports/basketball/nba/summary?event=<id>`` carries
everything an end-of-quarter post needs in one payload:

- ``header.competitions[0].competitors[].linescores`` — per-quarter
  scores (``boxscore.teams[].linescores`` is null on this feed; the
  header is where they live), summed through quarter N for the score
  at that buzzer.
- ``leaders`` — ESPN's *per-team* game leaders, grouped by team with
  the ``points``/``rebounds``/``assists`` categories the post prints.

Source note mirrors ``models/nba_live.py``: ESPN rather than the NBA's
own CDN, which blocks non-browser clients outright.
"""

from __future__ import annotations

from pydantic import Field

from models.base import BaseModel
from models.nba_live import NbaTeamInfo


class NbaSummaryLineScore(BaseModel):
    """One quarter's score: ``{"displayValue": "33"}``."""

    display_value: str | None = Field(default=None, alias="displayValue")


class NbaSummaryCompetitor(BaseModel):
    """A side's summary entry: which side + its per-quarter scores."""

    home_away: str | None = Field(default=None, alias="homeAway")  # "home"|"away"
    linescores: list[NbaSummaryLineScore] | None = None


class NbaSummaryCompetition(BaseModel):
    competitors: list[NbaSummaryCompetitor] = Field(default_factory=list)


class NbaSummaryHeader(BaseModel):
    competitions: list[NbaSummaryCompetition] = Field(default_factory=list)


class NbaLeaderAthlete(BaseModel):
    """Leader reference: display name first, full name as fallback."""

    display_name: str | None = Field(default=None, alias="displayName")
    full_name: str | None = Field(default=None, alias="fullName")


class NbaLeader(BaseModel):
    """One player leading a category: the value and who posted it."""

    display_value: str | None = Field(default=None, alias="displayValue")
    value: float | None = None
    athlete: NbaLeaderAthlete | None = None


class NbaCategoryLeaders(BaseModel):
    """One category's leaders (``name``: "points" | "rebounds" | "assists")."""

    name: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    leaders: list[NbaLeader] = Field(default_factory=list)


class NbaTeamLeaders(BaseModel):
    """Per-team game leaders — ESPN groups the categories by team."""

    team: NbaTeamInfo | None = None
    leaders: list[NbaCategoryLeaders] = Field(default_factory=list)


class NbaSummaryResponse(BaseModel):
    header: NbaSummaryHeader | None = None
    leaders: list[NbaTeamLeaders] = Field(default_factory=list)
