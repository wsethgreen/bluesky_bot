from pydantic import ConfigDict, Field

from models.base import BaseModel


class NhlScheduleTeam(BaseModel):
    id: int | None = None
    abbrev: str | None = None
    logo: str | None = None
    score: int | None = None


class NhlScheduleGame(BaseModel):
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


class NhlScheduleDay(BaseModel):
    date: str | None = None
    day_abbrev: str | None = Field(default=None, alias="dayAbbrev")
    number_of_games: int = Field(default=0, alias="numberOfGames")
    games: list[NhlScheduleGame] = Field(default_factory=list)


class NhlScheduleResponse(BaseModel):
    previous_start_date: str | None = Field(default=None, alias="previousStartDate")
    next_start_date: str | None = Field(default=None, alias="nextStartDate")
    number_of_games: int = Field(default=0, alias="numberOfGames")
    game_week: list[NhlScheduleDay] = Field(default_factory=list, alias="gameWeek")


class NhlPlayPeriod(BaseModel):
    number: int | None = None
    period_type: str | None = Field(default=None, alias="periodType")


class NhlPlayDetails(BaseModel):
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
    # Penalty plays ("penalty" typeDescKey): the offending team is the
    # event owner; descKey is the infraction ("tripping"), duration the
    # assessed minutes (2/4/5/10), and penaltyType MIN/MAJ/MIS.
    desc_key: str | None = Field(default=None, alias="descKey")
    duration: int | None = None
    penalty_type: str | None = Field(default=None, alias="typeCode")
    committed_by_player_id: int | None = Field(
        default=None, alias="committedByPlayerId"
    )
    drawn_by_player_id: int | None = Field(default=None, alias="drawnByPlayerId")
    away_score: int | None = Field(default=None, alias="awayScore")
    home_score: int | None = Field(default=None, alias="homeScore")
    highlight_clip: int | None = Field(default=None, alias="highlightClip")
    highlight_clip_sharing_url: str | None = Field(
        default=None, alias="highlightClipSharingUrl"
    )


class NhlPlay(BaseModel):
    event_id: int | None = Field(default=None, alias="eventId")
    period: NhlPlayPeriod | None = Field(default=None, alias="periodDescriptor")
    time_in_period: str | None = Field(default=None, alias="timeInPeriod")
    time_remaining: str | None = Field(default=None, alias="timeRemaining")
    type_code: int | None = Field(default=None, alias="typeCode")
    type_desc_key: str | None = Field(default=None, alias="typeDescKey")
    situation_code: str | None = Field(default=None, alias="situationCode")
    details: NhlPlayDetails | None = None


class NhlPlayByPlayTeam(BaseModel):
    """Header entry for one side of a play-by-play payload.

    ``score``/``sog`` are the NHL's own live counts (a goal counts as a
    shot on goal there too), so no local event tally is needed.
    """

    id: int | None = None
    abbrev: str | None = None
    score: int | None = None
    sog: int | None = None


class NhlGameClock(BaseModel):
    time_remaining: str | None = Field(default=None, alias="timeRemaining")
    seconds_remaining: int | None = Field(default=None, alias="secondsRemaining")
    running: bool | None = None
    in_intermission: bool | None = Field(default=None, alias="inIntermission")


class NhlPlayByPlay(BaseModel):
    id: int | None = None
    game_state: str | None = Field(default=None, alias="gameState")
    period_descriptor: NhlPlayPeriod | None = Field(
        default=None, alias="periodDescriptor"
    )
    away_team: NhlPlayByPlayTeam | None = Field(default=None, alias="awayTeam")
    home_team: NhlPlayByPlayTeam | None = Field(default=None, alias="homeTeam")
    clock: NhlGameClock | None = None
    plays: list[NhlPlay] = Field(default_factory=list)


class NhlTeamTotals(BaseModel):
    """One side's identity and official counts from the pbp header.

    ``None`` fields mean the payload didn't carry that side (never for
    a real game — the gamecenter UI reads the same fields).
    """

    model_config = ConfigDict(frozen=True)

    team_id: int | None = None
    abbrev: str | None = None
    score: int | None = None
    sog: int | None = None


class SkaterStrength(BaseModel):
    """On-ice skater counts from one situation code.

    Goalie digits are intentionally dropped: posts only care about the
    skater matchup (5 on 4, 4 on 4, …). Codes that don't parse produce
    no ``SkaterStrength`` at all. Frozen: strength states are values,
    never mutated in place.
    """

    model_config = ConfigDict(frozen=True)

    away_skaters: int
    home_skaters: int

    def split_for(
        self, *, team_id: int, home_team_id: int, away_team_id: int
    ) -> tuple[int, int]:
        """``(ours, theirs)`` skater counts from this team's perspective.

        ``(-1, -1)`` when the team is neither side of the matchup.
        """
        if team_id == home_team_id:
            return self.home_skaters, self.away_skaters
        if team_id == away_team_id:
            return self.away_skaters, self.home_skaters
        return -1, -1


class NhlPenalty(BaseModel):
    """One ``penalty`` play plus the strength that resulted from it.

    ``strength`` comes from the next faceoff after the call (the feed
    settles offsetting/coincidental minors there), or — when the
    penalty is the newest play in the payload — is simulated from the
    penalty's own situation code. See
    :class:`services.nhl_game_translator.NhlGameTranslator`.
    ``None`` only when neither works (unparseable codes).

    ``event_id`` (on the play) is what the posts table dedupes on.
    """

    model_config = ConfigDict(frozen=True)

    play: NhlPlay
    infraction: str | None
    minutes: int | None
    penalty_type: str | None  # MIN / MAJ / MIS from details.typeCode
    offending_team_id: int | None
    committed_by_player_id: int | None
    strength: SkaterStrength | None


class NhlLiveSnapshot(BaseModel):
    """Everything one play-by-play fetch tells the poller.

    The scoreboard counts come from the payload's own ``awayTeam``/
    ``homeTeam`` headers (the NHL's official score and shots-on-goal),
    so no local shot-event tally is needed. ``period_ends`` are the
    ``period-end`` plays in sequence — each carries the ``event_id``
    the posts table dedupes on. ``penalties`` likewise: one record per
    ``penalty`` play, in sequence, each with its resulting strength.
    """

    model_config = ConfigDict(frozen=True)

    game_state: str | None
    goals: list[NhlPlay]  # the followed team's goals, in sequence
    period_ends: list[NhlPlay]  # one play per finished period
    penalties: list[NhlPenalty]  # every penalty called, in sequence
    home: NhlTeamTotals
    away: NhlTeamTotals
