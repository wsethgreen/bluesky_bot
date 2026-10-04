from pydantic import Field, model_validator

from models.base import _Base


def _local(value: object) -> str:
    """Localized ``{"default": ...}`` dicts collapse to their default text."""
    if isinstance(value, dict):
        return str(value.get("default") or "")
    return str(value or "")


class NhlPlayer(_Base):
    """Flat player summary for post text + the players cache.

    Flattens the ``/v1/player/{id}/landing`` payload: names and team from
    the top level, current-season stats from
    ``featuredStats.regularSeason.subSeason``.
    """

    player_id: int | None = Field(default=None, alias="playerId")
    first_name: str = ""
    last_name: str = ""
    team_full_name: str = ""
    current_team_id: int | None = Field(default=None, alias="currentTeamId")
    current_team_abbrev: str | None = Field(default=None, alias="currentTeamAbbrev")
    sweater_number: int | None = Field(default=None, alias="sweaterNumber")
    position: str | None = None
    headshot: str | None = None
    is_active: bool | None = Field(default=None, alias="isActive")
    season: int | None = None
    games_played: int = 0
    goals: int = 0
    assists: int = 0
    points: int = 0
    plus_minus: int = 0
    power_play_goals: int = 0
    power_play_points: int = 0
    shorthanded_goals: int = 0
    shorthanded_points: int = 0

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        featured = data.get("featuredStats", {}) or {}
        sub = (featured.get("regularSeason", {}) or {}).get("subSeason", {}) or {}
        return {
            "player_id": data.get("playerId"),
            "first_name": _local(data.get("firstName")),
            "last_name": _local(data.get("lastName")),
            "team_full_name": _local(data.get("fullTeamName")),
            "current_team_id": data.get("currentTeamId"),
            "current_team_abbrev": data.get("currentTeamAbbrev"),
            "sweater_number": data.get("sweaterNumber"),
            "position": data.get("position"),
            "headshot": data.get("headshot"),
            "is_active": data.get("isActive"),
            "season": featured.get("season"),
            "games_played": sub.get("gamesPlayed") or 0,
            "goals": sub.get("goals") or 0,
            "assists": sub.get("assists") or 0,
            "points": sub.get("points") or 0,
            "plus_minus": sub.get("plusMinus") or 0,
            "power_play_goals": sub.get("powerPlayGoals") or 0,
            "power_play_points": sub.get("powerPlayPoints") or 0,
            "shorthanded_goals": sub.get("shorthandedGoals") or 0,
            "shorthanded_points": sub.get("shorthandedPoints") or 0,
        }
