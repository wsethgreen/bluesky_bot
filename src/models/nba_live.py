"""Models for the NBA scoreboard feed (ESPN site API).

``site.api.espn.com``'s scoreboard serves a day's NBA games with status,
scores, and team entries — the poller's source for game discovery and
live state (see ``clients/nba_live_client.py``).

Source note: the NBA's own ``cdn.nba.com`` liveData feed would carry
official NBA game/team ids, but it blocks every non-browser client
(Akamai 403 regardless of User-Agent — it fingerprints the TLS
handshake), so the bot reads ESPN instead. Ids parsed here are
therefore ESPN's own; NBA team rows namespace them into the ``teams``
table (see ``db/models/team.py``).
"""

from __future__ import annotations

from pydantic import Field, field_validator

from models.base import BaseModel


class NbaSeason(BaseModel):
    year: int | None = None
    # ESPN season type (2 = regular season); slug is the readable form.
    type: int | None = None
    slug: str | None = None


class NbaStatusType(BaseModel):
    """``status.type``: the state word plus a human-readable detail."""

    name: str | None = None  # e.g. "STATUS_FINAL"
    state: str | None = None  # "pre" | "in" | "post"
    completed: bool | None = None
    detail: str | None = None  # e.g. "Final", "Postponed"
    short_detail: str | None = Field(default=None, alias="shortDetail")


class NbaGameStatus(BaseModel):
    clock: float | None = None
    display_clock: str | None = Field(default=None, alias="displayClock")
    period: int | None = None
    type: NbaStatusType | None = None


class NbaTeamInfo(BaseModel):
    id: int | None = None  # ESPN team id (numeric string in JSON)
    abbreviation: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    location: str | None = None
    name: str | None = None
    short_display_name: str | None = Field(default=None, alias="shortDisplayName")


class NbaCompetitor(BaseModel):
    home_away: str | None = Field(default=None, alias="homeAway")  # "home"|"away"
    winner: bool | None = None
    # JSON string ("117"); absent/empty pregame → None.
    score: int | None = None
    team: NbaTeamInfo | None = None

    @field_validator("score", mode="before")
    @classmethod
    def _blank_score_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class NbaCompetition(BaseModel):
    id: int | None = None
    date: str | None = None
    neutral_site: bool | None = Field(default=None, alias="neutralSite")
    competitors: list[NbaCompetitor] = Field(default_factory=list)
    status: NbaGameStatus | None = None


class NbaScheduleGame(BaseModel):
    """One scoreboard event: an NBA game as ESPN sees it."""

    id: int | None = None
    date: str | None = None
    name: str | None = None
    short_name: str | None = Field(default=None, alias="shortName")
    season: NbaSeason | None = None
    status: NbaGameStatus | None = None
    competitions: list[NbaCompetition] = Field(default_factory=list)

    @property
    def competition(self) -> NbaCompetition | None:
        return self.competitions[0] if self.competitions else None

    @property
    def game_status(self) -> NbaGameStatus | None:
        """Authoritative status: the competition's, else the event's."""
        competition = self.competition
        if competition is not None and competition.status is not None:
            return competition.status
        return self.status

    @property
    def home_competitor(self) -> NbaCompetitor | None:
        competition = self.competition
        if competition is None:
            return None
        return next((c for c in competition.competitors if c.home_away == "home"), None)

    @property
    def away_competitor(self) -> NbaCompetitor | None:
        competition = self.competition
        if competition is None:
            return None
        return next((c for c in competition.competitors if c.home_away == "away"), None)

    def involves(self, team_abbreviation: str) -> bool:
        """Whether the team plays in this game, home or away (case-insensitive)."""
        team = team_abbreviation.strip().upper()
        for side in (self.home_competitor, self.away_competitor):
            info = side.team if side else None
            if info is not None and (info.abbreviation or "").upper() == team:
                return True
        return False


class NbaScoreboardResponse(BaseModel):
    events: list[NbaScheduleGame] = Field(default_factory=list)
