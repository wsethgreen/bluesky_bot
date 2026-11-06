"""Tests for PollWorker's row-state/score application.

The rule under test: the play-by-play header's official score (both
sides' goals, shootout adjustment included) wins over the last goal
play's ``details`` scores, which carry the tracked team's goals alone
— tonight's shootout (2026020258) left details frozen at 1-1 while
the header read 2-1, and 2026-10-18's game stored 1-1 for a 1-2 loss
because the opponent scored after the last tracked-team goal.
"""

from datetime import UTC, datetime

from db.models.game import Game
from jobs.poll_nhl_live import PollWorker
from models.nhl import NhlPlay


def make_game(
    *,
    game_state: str = "LIVE",
    home_score: int | None = None,
    away_score: int | None = None,
) -> Game:
    return Game(
        game_id=2026020258,
        league="nhl",
        season=20262027,
        game_type=2,
        start_time=datetime(2026, 11, 6, 0, 0, tzinfo=UTC),
        game_state=game_state,
        away_team_id=25,
        home_team_id=29,
        away_team_score=away_score,
        home_team_score=home_score,
    )


def goal_play(*, away_score: int, home_score: int) -> NhlPlay:
    return NhlPlay.model_validate(
        {
            "eventId": 992,
            "periodDescriptor": {"number": 3, "periodType": "REG"},
            "typeDescKey": "goal",
            "details": {"awayScore": away_score, "homeScore": home_score},
        }
    )


def apply(
    row: Game,
    *,
    state: str | None = "FINAL",
    goals: list[NhlPlay] | None = None,
    home_score: int | None = None,
    away_score: int | None = None,
) -> Game:
    PollWorker._apply_row_state(
        row, state, goals or [], home_score=home_score, away_score=away_score
    )
    return row


class TestApplyRowState:
    def test_header_score_wins_over_goal_details(self) -> None:
        # Shootout: the last goal's details say 1-1, the header 2-1.
        row = apply(
            make_game(),
            goals=[goal_play(away_score=1, home_score=1)],
            home_score=2,
            away_score=1,
        )
        assert row.home_team_score == 2
        assert row.away_team_score == 1

    def test_header_missing_falls_back_to_goal_details(self) -> None:
        row = apply(make_game(), goals=[goal_play(away_score=1, home_score=3)])
        assert row.home_team_score == 3
        assert row.away_team_score == 1

    def test_no_header_and_no_goals_keeps_stored_scores(self) -> None:
        row = apply(make_game(home_score=2, away_score=1))
        assert row.home_team_score == 2
        assert row.away_team_score == 1

    def test_terminal_state_stamps_ended_at_once(self) -> None:
        row = apply(make_game())
        assert row.game_state == "FINAL"
        assert row.ended_at is not None
        first = row.ended_at
        apply(row, state="OFF")
        assert row.ended_at == first

    def test_non_terminal_state_never_stamps(self) -> None:
        row = apply(make_game(), state="LIVE")
        assert row.game_state == "LIVE"
        assert row.ended_at is None
