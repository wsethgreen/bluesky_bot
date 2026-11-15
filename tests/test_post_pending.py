"""Tests for post_pending's pure helpers: video-resolver dispatch,
Bluesky credential dispatch, the posting-window check, and the
retry/backoff policy — plus the Brightcove "not ready" statuses, driven
through ``_nhl_mp4_url`` with a stub context.

All are deliberately sync (``asyncio.run`` around the resolver calls)
so they can be tested without the async/DB/network machinery around
``post_pending`` itself (the resolver lookups are async only to match
the VideoResolver signature).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx2
import pytest

from jobs.post_pending import (
    RETRY_MAX_DELAY,
    _account_for,
    _nba_mp4_url,
    _nhl_mp4_url,
    _resolver_for,
    _window_skip_reason,
    retry_decision,
    retry_delay,
)
from repos.posts_repo import MAX_ATTEMPTS


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


BRIGHTCOVE_URL = (
    "https://edge.api.brightcove.com/playback/v1/accounts/6415718365001/"
    "videos/6408395259112"
)


def _status_error(status: int) -> httpx2.HTTPStatusError:
    """The exception ``HttpClient.get`` raises for a non-2xx reply."""
    request = httpx2.Request("GET", BRIGHTCOVE_URL)
    response = httpx2.Response(status, request=request)
    return httpx2.HTTPStatusError(
        f"Client error '{status}' for url '{BRIGHTCOVE_URL}'",
        request=request,
        response=response,
    )


def _ctx(get_video: Callable[[int], Awaitable[object]]) -> SimpleNamespace:
    """WorkerContext stand-in exposing only what ``_nhl_mp4_url`` touches."""
    return SimpleNamespace(
        nhl=SimpleNamespace(brightcove=SimpleNamespace(get_video=get_video))
    )


def _ctx_raising(exc: Exception) -> SimpleNamespace:
    async def get_video(_video_id: int) -> object:
        raise exc

    return _ctx(get_video)


def _ctx_with_video(video: object) -> SimpleNamespace:
    async def get_video(_video_id: int) -> object:
        return video

    return _ctx(get_video)


class TestNhlClipNotReady:
    """Brightcove's answer for a brand-new clip is flaky, not fatal.

    A clip's playback policy takes a few minutes to propagate, and
    until it does the API answers 403 for a perfectly valid key — the
    transient that cost a highlight post. Those statuses mean "come
    back later", so they must not consume one of ``MAX_ATTEMPTS``.
    """

    post = SimpleNamespace(clip_id=6408395259112)

    @pytest.mark.parametrize("status", [403, 404])
    def test_not_ready_status_returns_none(self, status: int) -> None:
        ctx = _ctx_raising(_status_error(status))
        assert asyncio.run(_nhl_mp4_url(ctx, self.post)) is None  # type: ignore[arg-type]

    @pytest.mark.parametrize("status", [401, 429, 500, 503])
    def test_other_statuses_still_raise(self, status: int) -> None:
        # 401 means the policy key itself is bad and 5xx means
        # Brightcove is broken: both are real failures that must burn
        # an attempt toward MAX_ATTEMPTS.
        ctx = _ctx_raising(_status_error(status))
        with pytest.raises(httpx2.HTTPStatusError):
            asyncio.run(_nhl_mp4_url(ctx, self.post))  # type: ignore[arg-type]

    def test_missing_clip_id_raises_before_any_api_call(self) -> None:
        async def get_video(_video_id: int) -> object:
            raise AssertionError("get_video must not be called")

        with pytest.raises(ValueError, match="no clip_id"):
            asyncio.run(  # type: ignore[arg-type]
                _nhl_mp4_url(_ctx(get_video), SimpleNamespace(clip_id=None))
            )

    def test_mp4_rendition_is_returned(self) -> None:
        video = SimpleNamespace(
            sources=[
                SimpleNamespace(container="M3U8", src="https://cdn.test/x.m3u8"),
                SimpleNamespace(container="MP4", src="https://cdn.test/x.mp4"),
            ]
        )
        assert (
            asyncio.run(_nhl_mp4_url(_ctx_with_video(video), self.post))  # type: ignore[arg-type]
            == "https://cdn.test/x.mp4"
        )

    def test_no_mp4_rendition_is_not_ready(self) -> None:
        video = SimpleNamespace(
            sources=[SimpleNamespace(container="M3U8", src="https://cdn.test/x.m3u8")]
        )
        assert asyncio.run(_nhl_mp4_url(_ctx_with_video(video), self.post)) is None  # type: ignore[arg-type]

    def test_no_sources_at_all_is_not_ready(self) -> None:
        assert (
            asyncio.run(
                _nhl_mp4_url(_ctx_with_video(SimpleNamespace(sources=None)), self.post)
            )
            is None
        )  # type: ignore[arg-type]


class TestRetryDecision:
    """Backoff schedule: spread the attempts out, then give up loudly."""

    now = datetime(2026, 11, 14, 0, 27, tzinfo=UTC)

    def test_first_failure_waits_three_minutes(self) -> None:
        defunct, next_attempt_at = retry_decision(1, self.now)
        assert defunct is False
        assert next_attempt_at == self.now + timedelta(minutes=3)

    def test_delays_double_per_failure(self) -> None:
        assert [retry_delay(n) for n in (1, 2, 3, 4)] == [
            timedelta(minutes=minutes) for minutes in (3, 6, 12, 24)
        ]

    def test_attempts_stretch_over_three_quarters_of_an_hour(self) -> None:
        # The point of the backoff: five attempts used to burn through
        # in ~4 minutes (one per scheduler run) — far too short for a
        # transient Brightcove 403 to clear.
        total = sum((retry_delay(n) for n in range(1, MAX_ATTEMPTS)), timedelta())
        assert total == timedelta(minutes=45)

    def test_attempt_before_the_limit_still_schedules_a_retry(self) -> None:
        defunct, next_attempt_at = retry_decision(MAX_ATTEMPTS - 1, self.now)
        assert defunct is False
        assert next_attempt_at == self.now + timedelta(minutes=24)

    def test_last_attempt_gives_up(self) -> None:
        defunct, next_attempt_at = retry_decision(MAX_ATTEMPTS, self.now)
        assert defunct is True
        assert next_attempt_at is None

    def test_gap_is_capped(self) -> None:
        # Raising MAX_ATTEMPTS later must not spin the gap out to hours.
        assert retry_delay(10) == RETRY_MAX_DELAY

    def test_attempts_is_one_based(self) -> None:
        with pytest.raises(ValueError, match="attempts must be >= 1"):
            retry_delay(0)
