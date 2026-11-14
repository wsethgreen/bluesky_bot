"""Post-game video queueing: feed selection and the poller's claim path.

The NBA poller claims ``VIDEO`` posts from the team-site feed after
FINAL (game/player highlights and pressers, matched via
``taxonomy.games``). The selection helpers are pure; the claim path is
driven against a fake session so no Postgres or network is involved.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import Select
from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect

from db.models.game import Game
from jobs.poll_nba_live import NbaPollWorker
from models.nba_content import NbaContentVideo

# A 7:00 PM ET tip on 11/11 is 2026-11-12T00:00Z — the queue dates
# games in Eastern, which is what the feed's labels use.
GAME_DAY = datetime(2026, 11, 12, tzinfo=UTC)
GAME_ET_DATE = date(2026, 11, 11)
GAME_ID = 401909967
LABEL = "11/11/2026 CLE@NYK 0022600218"


def make_video(
    video_id: int,
    title: str,
    *,
    cats: tuple[str, ...] = ("game-highlights", "gameday"),
    game_label: str | None = LABEL,
    duration_ms: int = 180_000,
    with_assets: bool = True,
    description: str | None = None,
) -> NbaContentVideo:
    """A real-shaped feed item: renditions keyed by width + taxonomy."""
    payload: dict = {"id": video_id, "title": title}
    if description is not None:
        payload["description"] = description
    if with_assets:
        payload["videoAssets"] = {
            "960": {
                "width": 960,
                "height": 540,
                "video": f"https://cdn.nba.com/{video_id}-960.mp4",
                "duration": duration_ms,
            },
            "1280": {
                "width": 1280,
                "height": 720,
                "video": f"https://cdn.nba.com/{video_id}-720.mp4",
                "duration": duration_ms,
            },
            "duration": duration_ms,
        }
    payload["taxonomy"] = {
        "categories": {slug: slug for slug in cats},
        "games": {"0022600218": game_label} if game_label else {},
    }
    return NbaContentVideo.model_validate(payload)


HIGHLIGHT = make_video(1159204, "Cavs at Knicks | Game Highlights | 11.11.2026")
PRESSER = make_video(
    1159201,
    "Cavs at Knicks | Kenny Atkinson Post Game | 11.11.2026",
    cats=("gameday", "media-availability"),
)


class TestVideoMatchesGame:
    def test_matching_date_and_matchup(self) -> None:
        assert NbaPollWorker.video_matches_game(
            HIGHLIGHT, game_day=GAME_ET_DATE, away_tri="CLE", home_tri="NYK"
        )

    def test_home_game_matchup_order(self) -> None:
        # "Cavs vs Knicks" labels as NYK@CLE (away first), same as ESPN.
        video = make_video(1, "x", game_label="11/07/2026 NYK@CLE 0022600191")
        assert NbaPollWorker.video_matches_game(
            video,
            game_day=datetime(2026, 11, 7, tzinfo=UTC).date(),
            away_tri="NYK",
            home_tri="CLE",
        )

    def test_wrong_date_never_matches(self) -> None:
        assert not NbaPollWorker.video_matches_game(
            HIGHLIGHT,
            game_day=datetime(2026, 11, 12, tzinfo=UTC).date(),
            away_tri="CLE",
            home_tri="NYK",
        )

    def test_wrong_matchup_never_matches(self) -> None:
        assert not NbaPollWorker.video_matches_game(
            HIGHLIGHT, game_day=GAME_DAY.date(), away_tri="CLE", home_tri="CHA"
        )

    def test_video_without_game_tag_never_matches(self) -> None:
        video = make_video(1, "x", game_label=None)
        assert not NbaPollWorker.video_matches_game(
            video, game_day=GAME_DAY.date(), away_tri="CLE", home_tri="NYK"
        )

    def test_unparsable_label_is_skipped_not_raised(self) -> None:
        video = make_video(1, "x", game_label="someday CLE@NYK 0022600218")
        assert not NbaPollWorker.video_matches_game(
            video, game_day=GAME_DAY.date(), away_tri="CLE", home_tri="NYK"
        )


class TestIsPostableNbaVideo:
    def test_game_highlights_pass(self) -> None:
        assert NbaPollWorker.is_postable_nba_video(HIGHLIGHT, max_duration_seconds=600)

    def test_player_highlights_pass_via_category(self) -> None:
        video = make_video(2, "Cavs at Knicks | James Harden Highlights | 11.11.2026")
        assert NbaPollWorker.is_postable_nba_video(video, max_duration_seconds=600)

    def test_post_game_presser_passes_via_title(self) -> None:
        assert NbaPollWorker.is_postable_nba_video(PRESSER, max_duration_seconds=600)

    def test_shootaround_presser_is_skipped(self) -> None:
        # Pre-game media availability: "Media Availability" but no
        # "Post Game" in the title.
        video = make_video(
            3,
            "Cavs Shootaround | Sam Merrill Media Availability | 11.02.2026",
            cats=("gameday", "media-availability"),
        )
        assert not NbaPollWorker.is_postable_nba_video(video, max_duration_seconds=600)

    def test_docuseries_is_skipped(self) -> None:
        video = make_video(
            4, "The Road Back - Season 7, Episode 2", cats=("the-road-back",)
        )
        assert not NbaPollWorker.is_postable_nba_video(video, max_duration_seconds=600)

    def test_duration_over_bluesky_cap_is_skipped(self) -> None:
        # Observed pressers run 10:31 and 11:24 — over Bluesky's 10 min.
        video = make_video(5, "x | Post Game | 11.11.2026", duration_ms=631_000)
        assert not NbaPollWorker.is_postable_nba_video(video, max_duration_seconds=600)

    def test_duration_at_cap_passes(self) -> None:
        video = make_video(6, "x | Post Game | 11.11.2026", duration_ms=600_000)
        assert NbaPollWorker.is_postable_nba_video(video, max_duration_seconds=600)

    def test_video_without_assets_is_skipped(self) -> None:
        video = make_video(7, "x | Post Game | 11.11.2026", with_assets=False)
        assert not NbaPollWorker.is_postable_nba_video(video, max_duration_seconds=600)


class TestSelectPostableVideos:
    def test_filters_and_keeps_feed_order(self) -> None:
        other_game = make_video(
            9,
            "Cavs at Hornets | Game Highlights | 11.09.2026",
            game_label="11/09/2026 CLE@CHA 0022600204",
        )
        too_long = make_video(
            10,
            "Cavs at Knicks | Kenny Atkinson Post Game | 11.11.2026",
            cats=("gameday", "media-availability"),
            duration_ms=700_000,
        )
        selected = NbaPollWorker.select_postable_videos(
            [other_game, HIGHLIGHT, too_long, PRESSER],
            game_day=GAME_ET_DATE,
            away_tri="CLE",
            home_tri="NYK",
            max_duration_seconds=600,
        )
        assert [video.id for video in selected] == [HIGHLIGHT.id, PRESSER.id]


class FakeResult:
    def __init__(self, rows=(), rowcount: int = 0) -> None:
        self._rows = list(rows)
        self.rowcount = rowcount

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    """Enough AsyncSession for PostsRepo: Select answers ``existing``,
    everything else (the inserts) reports rowcount=1 (claimed)."""

    def __init__(self, existing: tuple[int, ...] = ()) -> None:
        self._existing = existing
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        if isinstance(stmt, Select):
            return FakeResult(rows=[(event_id,) for event_id in self._existing])
        return FakeResult(rowcount=1)


class StubContent:
    def __init__(self, items: list[NbaContentVideo]) -> None:
        self._items = items
        self.calls: list[dict] = []

    async def get_latest_videos(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(results=SimpleNamespace(items=self._items))


def make_worker(
    items: list[NbaContentVideo], *, grace: timedelta = timedelta(minutes=180)
) -> NbaPollWorker:
    # Bypass __init__: it builds real API clients from settings; the
    # queue path only needs the stub content client and the knobs.
    worker = NbaPollWorker.__new__(NbaPollWorker)
    worker._nba = SimpleNamespace(content=StubContent(items))  # type: ignore[assignment]
    worker._team = "CLE"
    worker._video_grace = grace
    worker._video_max_duration_seconds = 600
    return worker


def final_game(*, ended_at: datetime | None = None) -> Game:
    return Game(
        game_id=GAME_ID,
        league="nba",
        start_time=GAME_DAY,
        game_state="FINAL",
        ended_at=ended_at if ended_at is not None else datetime.now(UTC),
    )


def claim_params(session: FakeSession) -> list[dict]:
    """Bound params of each executed insert (the claim calls)."""
    return [
        dict(stmt.compile(dialect=asyncpg_dialect.dialect()).params)
        for stmt in session.statements
        if not isinstance(stmt, Select)
    ]


class TestQueuePostGameVideos:
    def test_claims_matching_highlights_and_pressers(self) -> None:
        worker = make_worker([HIGHLIGHT, PRESSER])
        session = FakeSession()

        count = asyncio.run(
            worker._queue_post_game_videos(session, final_game(), "CLE", "NYK")
        )

        assert count == 2
        params = claim_params(session)
        assert {p["event_id"] for p in params} == {HIGHLIGHT.id, PRESSER.id}
        by_id = {p["event_id"]: p for p in params}
        highlight = by_id[HIGHLIGHT.id]
        assert highlight["kind"] == "video"
        assert highlight["team"] == "CLE"
        assert highlight["game_id"] == GAME_ID
        assert highlight["clip_id"] == HIGHLIGHT.id
        # Best rendition (720p) captured at claim time for the poster.
        assert (
            highlight["highlight_url"] == f"https://cdn.nba.com/{HIGHLIGHT.id}-720.mp4"
        )
        # Caption: the feed's description prose (title as fallback)
        # plus the #LetEmKnow campaign tag.
        assert HIGHLIGHT.title in highlight["text"]
        assert "#CLE" not in highlight["text"]
        assert "#LetEmKnow" in highlight["text"]

    def test_caption_prefers_description_over_title(self) -> None:
        video = make_video(
            42,
            "Cavs at Knicks | Game Highlights | 11.11.2026",
            description=(
                "Cavs guard James Harden recorded his first 30-point "
                "game of the season in the win over the Knicks."
            ),
        )
        worker = make_worker([video])
        session = FakeSession()

        asyncio.run(worker._queue_post_game_videos(session, final_game(), "CLE", "NYK"))

        text = claim_params(session)[0]["text"]
        assert "first 30-point game" in text  # the description, not the title
        assert video.title not in text
        assert not text.startswith("🎥")  # no emoji prefix on NBA captions
        assert text.endswith("\n\n#LetEmKnow")
        assert "#CLE" not in text

    def test_overlong_description_is_trimmed_under_bluesky_cap(self) -> None:
        # A runaway description must not push the post over Bluesky's
        # 300-char cap (the API would reject it and the post would
        # never publish).
        video = make_video(43, "x", description="a" * 400)
        worker = make_worker([video])
        session = FakeSession()

        asyncio.run(worker._queue_post_game_videos(session, final_game(), "CLE", "NYK"))

        text = claim_params(session)[0]["text"]
        assert len(text) <= 300
        assert text.endswith("#LetEmKnow")

    def test_skips_already_queued_video_ids(self) -> None:
        worker = make_worker([HIGHLIGHT, PRESSER])
        session = FakeSession(existing=(HIGHLIGHT.id,))

        count = asyncio.run(
            worker._queue_post_game_videos(session, final_game(), "CLE", "NYK")
        )

        assert count == 1
        assert [p["event_id"] for p in claim_params(session)] == [PRESSER.id]

    def test_videos_for_other_games_claim_nothing(self) -> None:
        other = make_video(
            9,
            "Cavs at Hornets | Game Highlights | 11.09.2026",
            game_label="11/09/2026 CLE@CHA 0022600204",
        )
        worker = make_worker([other])
        session = FakeSession()

        count = asyncio.run(
            worker._queue_post_game_videos(session, final_game(), "CLE", "NYK")
        )

        assert count == 0
        assert session.statements == []  # no candidates → no posts read

    def test_no_feed_items_means_no_db_reads(self) -> None:
        worker = make_worker([])
        session = FakeSession()
        assert (
            asyncio.run(
                worker._queue_post_game_videos(session, final_game(), "CLE", "NYK")
            )
            == 0
        )
        assert session.statements == []

    def test_unfinished_game_never_touches_the_feed(self) -> None:
        worker = make_worker([HIGHLIGHT])
        session = FakeSession()
        row = final_game()
        row.game_state = "LIVE"

        count = asyncio.run(worker._queue_post_game_videos(session, row, "CLE", "NYK"))

        assert count is None
        assert worker._nba.content.calls == []  # type: ignore[attr-defined]
        assert session.statements == []

    def test_grace_window_closed_returns_none_without_calls(self) -> None:
        worker = make_worker([HIGHLIGHT])
        session = FakeSession()
        row = final_game(ended_at=datetime.now(UTC) - timedelta(minutes=181))

        count = asyncio.run(worker._queue_post_game_videos(session, row, "CLE", "NYK"))

        assert count is None
        assert worker._nba.content.calls == []  # type: ignore[attr-defined]

    def test_inside_grace_window_checks_the_feed(self) -> None:
        worker = make_worker([HIGHLIGHT])
        session = FakeSession()
        row = final_game(ended_at=datetime.now(UTC) - timedelta(minutes=30))

        count = asyncio.run(worker._queue_post_game_videos(session, row, "CLE", "NYK"))

        assert count == 1

    def test_force_bypasses_the_grace_window(self) -> None:
        worker = make_worker([HIGHLIGHT])
        session = FakeSession()
        row = final_game(ended_at=datetime.now(UTC) - timedelta(days=3))

        count = asyncio.run(
            worker._queue_post_game_videos(session, row, "CLE", "NYK", force=True)
        )

        assert count == 1

    def test_zero_grace_disables_video_queueing(self) -> None:
        worker = make_worker([HIGHLIGHT], grace=timedelta(0))
        session = FakeSession()

        count = asyncio.run(
            worker._queue_post_game_videos(session, final_game(), "CLE", "NYK")
        )

        assert count is None
        assert worker._nba.content.calls == []  # type: ignore[attr-defined]

    def test_legacy_final_row_without_ended_at_starts_its_window(self) -> None:
        # A row stored FINAL before ended_at existed must start a
        # bounded window now — never poll the feed forever.
        worker = make_worker([HIGHLIGHT])
        session = FakeSession()
        row = final_game(ended_at=None)

        count = asyncio.run(worker._queue_post_game_videos(session, row, "CLE", "NYK"))

        assert count == 1
        assert row.ended_at is not None

    def test_naive_ended_at_is_read_as_utc(self) -> None:
        worker = make_worker([])
        session = FakeSession()
        row = final_game(ended_at=datetime.now(UTC).replace(tzinfo=None))

        # Inside the window (30 min ago) rather than raising on the
        # naive/aware comparison or misreading it as decades off.
        assert (
            asyncio.run(worker._queue_post_game_videos(session, row, "CLE", "NYK")) == 0
        )
