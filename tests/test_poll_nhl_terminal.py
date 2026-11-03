"""Tests for PollWorker's data-driven terminal short-circuit.

The decision helper is pure (Game row + count in, reason or None out),
so it's testable without the async/DB/NHL machinery around it.
"""

from datetime import UTC, datetime, timedelta

from db.models.game import Game
from jobs.poll_nhl_live import PollWorker


def make_game(*, ended_at: datetime | None = None) -> Game:
    return Game(
        game_id=2026020001,
        league="nhl",
        season=20262027,
        game_type=2,
        start_time=datetime(2026, 11, 3, 0, 0, tzinfo=UTC),
        game_state="FINAL",
        away_team_id=1,
        home_team_id=2,
        ended_at=ended_at,
    )


def worker(*, grace_minutes: int = 15) -> PollWorker:
    # None deps are fine: the helpers under test touch no NHL/DB state.
    return PollWorker(
        nhl=None,  # type: ignore[arg-type]
        sessions=None,  # type: ignore[arg-type]
        team_abbreviation="CBJ",
        final_grace=timedelta(minutes=grace_minutes),
    )


class TestTerminalStopReason:
    def test_no_pending_clips_stops_immediately(self) -> None:
        # Clips resolved (or a 0-0 game): nothing after the final horn
        # can change — exit without spending an API call, even inside
        # the grace window.
        reason = worker()._terminal_stop_reason(
            make_game(ended_at=datetime.now(UTC)), 0
        )
        assert reason is not None
        assert "no highlight clips pending" in reason

    def test_pending_clip_inside_grace_keeps_polling(self) -> None:
        assert (
            worker()._terminal_stop_reason(make_game(ended_at=datetime.now(UTC)), 2)
            is None
        )

    def test_pending_clip_just_inside_grace_keeps_polling(self) -> None:
        ended = datetime.now(UTC) - timedelta(minutes=14)
        assert worker()._terminal_stop_reason(make_game(ended_at=ended), 1) is None

    def test_pending_clip_past_grace_stops(self) -> None:
        # The hard cap: a clip that never arrives can't poll forever.
        ended = datetime.now(UTC) - timedelta(minutes=16)
        reason = worker()._terminal_stop_reason(make_game(ended_at=ended), 1)
        assert reason is not None
        assert "after grace window" in reason

    def test_missing_ended_at_counts_as_inside_grace(self) -> None:
        # Legacy/late-stamped rows: keep polling so the cycle stamps
        # ended_at and the window runs from there.
        assert worker()._terminal_stop_reason(make_game(), 1) is None

    def test_zero_grace_disables_the_wait(self) -> None:
        reason = worker(grace_minutes=0)._terminal_stop_reason(
            make_game(ended_at=datetime.now(UTC)), 1
        )
        assert reason is not None
        assert "after grace window" in reason
