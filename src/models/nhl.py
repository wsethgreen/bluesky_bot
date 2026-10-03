from pydantic import Field

from models.base import _Base


class NhlScheduleTeam(_Base):
    id: int | None = None
    abbrev: str | None = None
    logo: str | None = None
    score: int | None = None


class NhlScheduleGame(_Base):
    id: int | None = None
    season: int | None = None
    game_type: int | None = Field(default=None, alias="gameType")
    start_time_utc: str | None = Field(default=None, alias="startTimeUTC")
    game_state: str | None = Field(default=None, alias="gameState")
    game_schedule_state: str | None = Field(default=None, alias="gameScheduleState")
    away_team: NhlScheduleTeam | None = Field(default=None, alias="awayTeam")
    home_team: NhlScheduleTeam | None = Field(default=None, alias="homeTeam")

    def involves(self, team_abbreviation: str) -> bool:
        """Whether the team plays in this game, home or away (case-insensitive)."""
        team = team_abbreviation.strip().upper()
        sides = (self.home_team, self.away_team)
        return any(s is not None and (s.abbrev or "").upper() == team for s in sides)


class NhlScheduleDay(_Base):
    date: str | None = None
    day_abbrev: str | None = Field(default=None, alias="dayAbbrev")
    number_of_games: int = Field(default=0, alias="numberOfGames")
    games: list[NhlScheduleGame] = Field(default_factory=list)


class NhlScheduleResponse(_Base):
    previous_start_date: str | None = Field(default=None, alias="previousStartDate")
    next_start_date: str | None = Field(default=None, alias="nextStartDate")
    number_of_games: int = Field(default=0, alias="numberOfGames")
    game_week: list[NhlScheduleDay] = Field(default_factory=list, alias="gameWeek")


class NhlPlayPeriod(_Base):
    number: int | None = None
    period_type: str | None = Field(default=None, alias="periodType")


class NhlPlayDetails(_Base):
    scoring_player_id: int | None = Field(default=None, alias="scoringPlayerId")
    scoring_player_total: int | None = Field(default=None, alias="scoringPlayerTotal")
    assist1_player_id: int | None = Field(default=None, alias="assist1PlayerId")
    assist2_player_id: int | None = Field(default=None, alias="assist2PlayerId")
    shot_type: str | None = Field(default=None, alias="shotType")
    zone_code: str | None = Field(default=None, alias="zoneCode")
    x_coord: int | None = Field(default=None, alias="xCoord")
    y_coord: int | None = Field(default=None, alias="yCoord")
    event_owner_team_id: int | None = Field(default=None, alias="eventOwnerTeamId")
    goalie_in_net_id: int | None = Field(default=None, alias="goalieInNetId")
    away_score: int | None = Field(default=None, alias="awayScore")
    home_score: int | None = Field(default=None, alias="homeScore")
    highlight_clip: int | None = Field(default=None, alias="highlightClip")
    highlight_clip_sharing_url: str | None = Field(
        default=None, alias="highlightClipSharingUrl"
    )


class NhlPlay(_Base):
    event_id: int | None = Field(default=None, alias="eventId")
    period: NhlPlayPeriod | None = Field(default=None, alias="periodDescriptor")
    time_in_period: str | None = Field(default=None, alias="timeInPeriod")
    time_remaining: str | None = Field(default=None, alias="timeRemaining")
    type_code: int | None = Field(default=None, alias="typeCode")
    type_desc_key: str | None = Field(default=None, alias="typeDescKey")
    situation_code: str | None = Field(default=None, alias="situationCode")
    details: NhlPlayDetails | None = None


class NhlPlayByPlay(_Base):
    id: int | None = None
    game_state: str | None = Field(default=None, alias="gameState")
    plays: list[NhlPlay] = Field(default_factory=list)
