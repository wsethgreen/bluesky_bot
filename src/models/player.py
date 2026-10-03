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
    sweater_number: int | None = Field(default=None, alias="sweaterNumber")
    goals: int = 0
    assists: int = 0
    points: int = 0
    power_play_goals: int = 0
    power_play_points: int = 0
    shorthanded_goals: int = 0
    shorthanded_points: int = 0

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        sub = (
            data.get("featuredStats", {}).get("regularSeason", {}).get("subSeason", {})
            or {}
        )
        return {
            "player_id": data.get("playerId"),
            "first_name": _local(data.get("firstName")),
            "last_name": _local(data.get("lastName")),
            "team_full_name": _local(data.get("fullTeamName")),
            "current_team_id": data.get("currentTeamId"),
            "sweater_number": data.get("sweaterNumber"),
            "goals": sub.get("goals") or 0,
            "assists": sub.get("assists") or 0,
            "points": sub.get("points") or 0,
            "power_play_goals": sub.get("powerPlayGoals") or 0,
            "power_play_points": sub.get("powerPlayPoints") or 0,
            "shorthanded_goals": sub.get("shorthandedGoals") or 0,
            "shorthanded_points": sub.get("shorthandedPoints") or 0,
        }
