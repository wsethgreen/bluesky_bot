"""Shootout handling in the NHL service's goal queries.

The feed records shootout attempts as ``goal`` plays in period 5
(``periodType: "SO"``) — gamecenter 2026020258 (DAL @ CBJ, 2-1 SO)
had two of them — so every goal list must drop them while keeping the
shootout's ``period-end`` play, which the final post claims.
"""

import asyncio

from models.nhl import NhlPlay, NhlPlayByPlay
from services.nhl_game_translator import NhlGameTranslator
from services.nhl_service import NhlService, is_scored_goal, is_shootout_play

# Structurally faithful excerpt of gamecenter 2026020258's payload:
# one regulation CBJ goal, one shootout attempt recorded as a goal,
# and both period-ends (regulation's and the shootout's).
PBP = {
    "gameState": "FINAL",
    "periodDescriptor": {"number": 5, "periodType": "SO"},
    "awayTeam": {"id": 25, "abbrev": "DAL", "score": 1, "sog": 28},
    "homeTeam": {"id": 29, "abbrev": "CBJ", "score": 2, "sog": 30},
    "plays": [
        {
            "eventId": 992,
            "periodDescriptor": {"number": 3, "periodType": "REG"},
            "timeInPeriod": "16:46",
            "typeDescKey": "goal",
            "details": {"eventOwnerTeamId": 29, "awayScore": 1, "homeScore": 1},
        },
        {
            "eventId": 342,
            "periodDescriptor": {"number": 5, "periodType": "SO"},
            "timeInPeriod": "00:00",
            "typeDescKey": "goal",
            "details": {"eventOwnerTeamId": 29, "awayScore": 1, "homeScore": 1},
        },
        {
            "eventId": 325,
            "periodDescriptor": {"number": 3, "periodType": "REG"},
            "timeInPeriod": "20:00",
            "typeDescKey": "period-end",
        },
        {
            "eventId": 343,
            "periodDescriptor": {"number": 5, "periodType": "SO"},
            "timeInPeriod": "00:00",
            "typeDescKey": "period-end",
        },
    ],
}


class StubClient:
    """Just enough NhlClient for the methods under test (no HTTP)."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def get_play_by_play(self, game_id: int) -> NhlPlayByPlay:
        return NhlPlayByPlay.model_validate(self._payload)

    async def get_plays(self, game_id: int) -> list[NhlPlay]:
        return NhlPlayByPlay.model_validate(self._payload).plays


def service() -> NhlService:
    # Bypass __init__: it builds real API clients from settings, and
    # the methods under test only need the stub client + translator.
    svc = NhlService.__new__(NhlService)
    svc.client = StubClient(PBP)  # type: ignore[assignment]
    svc.game_translator = NhlGameTranslator()
    return svc


def play(payload: dict) -> NhlPlay:
    return NhlPlay.model_validate(payload)


class TestShootoutPredicates:
    def test_so_period_goal_is_not_a_scored_goal(self) -> None:
        attempt = play(
            {
                "eventId": 342,
                "periodDescriptor": {"number": 5, "periodType": "SO"},
                "typeDescKey": "goal",
            }
        )
        assert is_shootout_play(attempt)
        assert not is_scored_goal(attempt)

    def test_regulation_goal_is_scored(self) -> None:
        goal = play(
            {
                "eventId": 992,
                "periodDescriptor": {"number": 3, "periodType": "REG"},
                "typeDescKey": "goal",
            }
        )
        assert not is_shootout_play(goal)
        assert is_scored_goal(goal)

    def test_missing_period_is_not_treated_as_shootout(self) -> None:
        goal = play({"eventId": 1, "typeDescKey": "goal"})
        assert not is_shootout_play(goal)
        assert is_scored_goal(goal)

    def test_non_goal_play_is_never_a_scored_goal(self) -> None:
        period_end = play(
            {
                "eventId": 343,
                "periodDescriptor": {"number": 5, "periodType": "SO"},
                "typeDescKey": "period-end",
            }
        )
        assert not is_scored_goal(period_end)


class TestSnapshotExcludesShootout:
    def test_goals_skip_attempts_but_period_ends_stay(self) -> None:
        snap = asyncio.run(service().get_live_snapshot(2026020258, team_id=29))
        # Only the regulation goal — the period-5 attempt is out.
        assert [p.event_id for p in snap.goals] == [992]
        # The shootout's period-end survives: the final post claims
        # the last period-end of the game.
        assert [p.event_id for p in snap.period_ends] == [325, 343]
        assert snap.game_state == "FINAL"
        assert (snap.home.score, snap.away.score) == (2, 1)

    def test_get_goals_for_game_drops_shootout_attempts(self) -> None:
        goals = asyncio.run(service().get_goals_for_game(2026020258))
        assert [p.event_id for p in goals] == [992]
