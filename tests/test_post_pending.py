"""Tests for post_pending's pure helpers: video-resolver dispatch and
the posting-window check.

Both are deliberately sync so they can be tested without the
async/DB/network machinery around ``post_pending`` itself.
"""

from datetime import UTC, datetime, timedelta

import pytest

from jobs.post_pending import _nhl_mp4_url, _resolver_for, _window_skip_reason


class TestVideoResolverDispatch:
    def test_nhl_resolves_via_brightcove(self) -> None:
        assert _resolver_for("nhl") is _nhl_mp4_url

    def test_unknown_league_fails_loudly(self) -> None:
        # No NBA resolver until the NBA poller lands; guessing (e.g.
        # treating an NBA content id as a Brightcove id) would mis-post.
        with pytest.raises(ValueError, match="no video resolver for league 'nba'"):
            _resolver_for("nba")

    def test_lookup_is_exact_no_silent_normalization(self) -> None:
        # Pollers store leagues lowercase ("nhl", "nba"); a differently
        # cased value must raise, not quietly resolve to some source.
        with pytest.raises(ValueError, match="no video resolver"):
            _resolver_for("NHL")

    def test_error_names_the_known_leagues(self) -> None:
        with pytest.raises(ValueError, match=r"known: nhl"):
            _resolver_for("nfl")


class TestWindowSkipReason:
    start = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)

    def test_inside_window_returns_none(self) -> None:
        assert (
            _window_skip_reason(self.start, self.start + timedelta(hours=2), 4) is None
        )

    def test_before_game_start(self) -> None:
        reason = _window_skip_reason(self.start, self.start - timedelta(minutes=1), 4)
        assert reason is not None
        assert reason.startswith("before game start")

    def test_past_window(self) -> None:
        reason = _window_skip_reason(
            self.start, self.start + timedelta(hours=4, seconds=1), 4
        )
        assert reason is not None
        assert "outside 4h posting window" in reason

    def test_window_edges_are_inclusive(self) -> None:
        # kickoff itself and exactly window_hours later both still post
        assert _window_skip_reason(self.start, self.start, 4) is None
        assert (
            _window_skip_reason(self.start, self.start + timedelta(hours=4), 4) is None
        )

    def test_naive_start_is_treated_as_utc(self) -> None:
        # A tz-less DB timestamp must not raise against an aware now,
        # and must not be misread as "before game start".
        naive_start = datetime(2026, 11, 3, 0, 0)  # noqa: DTZ001 - tz-less on purpose
        now = datetime(2026, 11, 3, 1, 0, tzinfo=UTC)
        assert _window_skip_reason(naive_start, now, 4) is None
        reason = _window_skip_reason(naive_start, now - timedelta(hours=5), 4)
        assert reason is not None
        assert reason.startswith("before game start")

    def test_window_hours_is_configurable(self) -> None:
        now = self.start + timedelta(hours=2, minutes=1)
        assert _window_skip_reason(self.start, now, 2) is not None
        assert _window_skip_reason(self.start, now, 3) is None
