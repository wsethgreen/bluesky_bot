"""Tests for post_pending's pure helpers: video-resolver dispatch,
Bluesky credential dispatch, and the posting-window check.

Both are deliberately sync so they can be tested without the
async/DB/network machinery around ``post_pending`` itself (the two
resolver lookups are async only to match the VideoResolver signature).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from jobs.post_pending import (
    _account_for,
    _nba_mp4_url,
    _nhl_mp4_url,
    _resolver_for,
    _window_skip_reason,
)


class TestVideoResolverDispatch:
    def test_nhl_resolves_via_brightcove(self) -> None:
        assert _resolver_for("nhl") is _nhl_mp4_url

    def test_nba_resolves_via_captured_feed_url(self) -> None:
        # The poller captures the content feed's best-rendition MP4
        # into highlight_url at claim time; the resolver just reads it.
        assert _resolver_for("nba") is _nba_mp4_url

    def test_nba_row_without_url_fails_loudly(self) -> None:
        # A video row with no captured URL is malformed (claim always
        # stores one) — raise so the attempt counts toward MAX_ATTEMPTS.
        post = SimpleNamespace(highlight_url=None)
        with pytest.raises(ValueError, match="no highlight_url"):
            asyncio.run(_nba_mp4_url(None, post))  # type: ignore[arg-type]

    def test_nba_row_returns_captured_url(self) -> None:
        post = SimpleNamespace(highlight_url="https://cdn.nba.com/x.mp4")
        assert asyncio.run(_nba_mp4_url(None, post)) == "https://cdn.nba.com/x.mp4"  # type: ignore[arg-type]

    def test_unknown_league_fails_loudly(self) -> None:
        # No resolver for an unregistered league; guessing (e.g.
        # treating an unknown id as a Brightcove id) would mis-post.
        with pytest.raises(ValueError, match="no video resolver for league 'nfl'"):
            _resolver_for("nfl")

    def test_lookup_is_exact_no_silent_normalization(self) -> None:
        # Pollers store leagues lowercase ("nhl", "nba"); a differently
        # cased value must raise, not quietly resolve to some source.
        with pytest.raises(ValueError, match="no video resolver"):
            _resolver_for("NHL")

    def test_error_names_the_known_leagues(self) -> None:
        with pytest.raises(ValueError, match=r"known: nba, nhl"):
            _resolver_for("mlb")


class TestLeagueAccountDispatch:
    # Only group identity matters here; the stub stands in for Settings.
    settings = SimpleNamespace(cbj=object(), cavs=object())

    def test_nhl_uses_cbj_credentials(self) -> None:
        assert _account_for(self.settings, "nhl") is self.settings.cbj

    def test_nba_uses_cavs_credentials(self) -> None:
        assert _account_for(self.settings, "nba") is self.settings.cavs

    def test_unknown_league_fails_loudly(self) -> None:
        # No default account: posting to the wrong bot is the exact
        # mix-up this mapping exists to prevent.
        with pytest.raises(ValueError, match="no Bluesky account for league 'nfl'"):
            _account_for(self.settings, "nfl")

    def test_lookup_is_exact_no_silent_normalization(self) -> None:
        # Pollers store leagues lowercase; a differently cased value
        # must raise, not quietly post from some account.
        with pytest.raises(ValueError, match="no Bluesky account"):
            _account_for(self.settings, "NHL")

    def test_error_names_the_known_leagues(self) -> None:
        with pytest.raises(ValueError, match=r"known: nba, nhl"):
            _account_for(self.settings, "mlb")


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
