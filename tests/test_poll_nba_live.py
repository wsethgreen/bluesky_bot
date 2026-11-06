"""Tests for the NBA poller: scoreboard parsing, state mapping, row state.

Payload shapes mirror ESPN's real scoreboard (verified against
``site.api.espn.com`` live data): string ids/scores, ``status.type``
state words, and season objects whose ``year`` is the season *end*.
Everything under test is pure — no DB, no network, no settings.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from clients.nba_live_client import NbaLiveClient
from db.models.game import Game
from db.models.team import NBA_TEAM_ID_OFFSET, nba_team_row_id
from jobs.poll_nba_live import (
    NbaPollWorker,
    _score_line,
    _stop_reason,
    game_type_code,
    map_game_state,
    season_code,
)
from models.nba_live import NbaScoreboardResponse


def make_event(
    *,
    event_id: int = 401909936,
    date: str = "2026-11-06T02:00Z",
    state: str = "pre",
    completed: bool = False,
    detail: str = "7:00 PM ET",
    short_detail: str = "7:00 PM ET",
    away_abbrev: str = "CLE",
    away_id: int = 5,
    away_score: str | None = None,
    home_abbrev: str = "BOS",
    home_id: int = 2,
    home_score: str | None = None,
    season_year: int = 2027,
    season_slug: str = "regular-season",
) -> dict:
    """One real-shaped ESPN scoreboard event."""

    def competitor(home_away: str, abbrev: str, team_id: int, score: str | None):
        entry = {
            "homeAway": home_away,
            "winner": state == "post" and home_away == "home",
            "team": {
                "id": str(team_id),
                "abbreviation": abbrev,
                "displayName": f"City {abbrev}",
            },
        }
        if score is not None:  # absent pregame, string once played
            entry["score"] = score
        return entry

    return {
        "id": str(event_id),
        "date": date,
        "name": f"{away_abbrev} at {home_abbrev}",
        "shortName": f"{away_abbrev} @ {home_abbrev}",
        "season": {"year": season_year, "type": 2, "slug": season_slug},
        "status": {
            "clock": 0.0,
            "displayClock": "0:00",
            "period": 0,
            "type": {
                "name": "STATUS_SCHEDULED",
                "state": state,
                "completed": completed,
                "detail": detail,
                "shortDetail": short_detail,
            },
        },
        "competitions": [
            {
                "id": str(event_id),
                "date": date,
                "neutralSite": False,
                "competitors": [
                    competitor("away", away_abbrev, away_id, away_score),
                    competitor("home", home_abbrev, home_id, home_score),
                ],
                "status": {
                    "clock": 0.0,
                    "displayClock": "0:00",
                    "period": 4 if state == "post" else 0,
                    "type": {
                        "name": "STATUS_FINAL"
                        if state == "post"
                        else "STATUS_SCHEDULED",
                        "state": state,
                        "completed": completed,
                        "detail": detail,
                        "shortDetail": short_detail,
                    },
                },
            }
        ],
    }


def parse(*events: dict) -> list:
    payload = NbaScoreboardResponse.model_validate({"events": list(events)})
    return payload.events


def make_game(
    *,
    game_state: str | None = "LIVE",
    home_score: int | None = None,
    away_score: int | None = None,
    ended_at: datetime | None = None,
) -> Game:
    return Game(
        game_id=401909936,
        league="nba",
        season=20262027,
        game_type=2,
        start_time=datetime(2026, 11, 6, 0, 0, tzinfo=UTC),
        game_state=game_state,
        ended_at=ended_at,
        away_team_id=NBA_TEAM_ID_OFFSET + 5,
        home_team_id=NBA_TEAM_ID_OFFSET + 2,
        away_team_score=away_score,
        home_team_score=home_score,
    )


class TestScoreboardParsing:
    def test_involves_is_case_insensitive_on_both_sides(self) -> None:
        (game,) = parse(make_event())
        assert game.involves("CLE")
        assert game.involves(" bos ")
        assert not game.involves("PHX")

    def test_competitors_and_scores_parse(self) -> None:
        (game,) = parse(
            make_event(
                state="post",
                completed=True,
                detail="Final",
                away_score="104",
                home_score="117",
            )
        )
        assert game.home_competitor is not None
        assert game.away_competitor is not None
        assert game.home_competitor.score == 117  # JSON string → int
        assert game.away_competitor.score == 104

    def test_pregame_competitor_without_score_is_none(self) -> None:
        (game,) = parse(make_event())
        assert game.away_competitor is not None
        assert game.away_competitor.score is None

    def test_blank_score_string_is_none(self) -> None:
        (game,) = parse(make_event(state="in", away_score="  ", home_score="98"))
        assert game.away_competitor is not None
        assert game.away_competitor.score is None
        assert game.home_competitor is not None
        assert game.home_competitor.score == 98

    def test_game_status_prefers_competition_status(self) -> None:
        # Event-level status says pregame; the competition (where the
        # authoritative state lives) says live.
        event = make_event(state="in", completed=False, detail="Halftime")
        event["status"]["type"]["state"] = "pre"
        (game,) = parse(event)
        assert game.game_status is not None
        assert game.game_status.type is not None
        assert game.game_status.type.state == "in"

    def test_empty_scoreboard_has_no_events(self) -> None:
        payload = NbaScoreboardResponse.model_validate({})
        assert payload.events == []


class TestMapGameState:
    def test_state_words_map_to_nhl_vocabulary(self) -> None:
        assert map_game_state(_status(state="pre")) == "FUTURE"
        assert map_game_state(_status(state="in")) == "LIVE"
        assert map_game_state(_status(state="post", completed=True)) == "FINAL"

    def test_postponed_detail_wins_over_state(self) -> None:
        # Postponed must never store as FINAL (it would stamp ended_at
        # and stop the poller as if a game had been played).
        assert map_game_state(_status(state="pre", detail="Postponed")) == "POSTPONED"
        assert map_game_state(_status(state="post", detail="Postponed")) == "POSTPONED"

    def test_canceled_detail_maps(self) -> None:
        assert map_game_state(_status(state="post", detail="Canceled")) == "CANCELED"

    def test_missing_status_is_none_not_a_guess(self) -> None:
        assert map_game_state(None) is None
        assert map_game_state(SimpleNamespace(type=None)) is None

    def test_unknown_state_word_is_none(self) -> None:
        assert map_game_state(_status(state="weird")) is None


def _status(*, state: str, detail: str = "Final", completed: bool = False):
    from models.nba_live import NbaGameStatus

    return NbaGameStatus.model_validate(
        {
            "type": {
                "state": state,
                "detail": detail,
                "completed": completed,
                "shortDetail": detail,
            }
        }
    )


class TestApplyRowState:
    def test_scores_apply_and_none_leaves_stored_value(self) -> None:
        row = make_game(home_score=50, away_score=44)
        NbaPollWorker._apply_row_state(row, "LIVE", home_score=77, away_score=None)
        assert row.home_team_score == 77
        assert row.away_team_score == 44  # None must not erase a score

    def test_terminal_final_stamps_ended_at_once(self) -> None:
        row = make_game()
        NbaPollWorker._apply_row_state(row, "FINAL", home_score=117, away_score=104)
        assert row.game_state == "FINAL"
        assert row.ended_at is not None
        first = row.ended_at
        NbaPollWorker._apply_row_state(row, "FINAL")
        assert row.ended_at == first

    def test_non_final_states_never_stamp(self) -> None:
        for state in ("LIVE", "FUTURE", "POSTPONED", "CANCELED"):
            row = make_game(game_state=None)
            NbaPollWorker._apply_row_state(row, state)
            assert row.ended_at is None, state

    def test_unmappable_state_keeps_stored_value(self) -> None:
        row = make_game(game_state="LIVE")
        NbaPollWorker._apply_row_state(row, None)
        assert row.game_state == "LIVE"


class TestStopReason:
    def test_settled_states_stop(self) -> None:
        for state in ("FINAL", "POSTPONED", "CANCELED"):
            reason = _stop_reason(state)
            assert reason is not None, state
            assert state.lower() in reason

    def test_mutable_states_keep_polling(self) -> None:
        assert _stop_reason("LIVE") is None
        assert _stop_reason("FUTURE") is None
        assert _stop_reason(None) is None


class TestCodes:
    def test_season_code_is_nhl_shaped(self) -> None:
        # ESPN's year is the season END (2027 = the 2026-27 season).
        assert season_code(2027) == 20262027

    def test_game_type_prefers_slug(self) -> None:
        assert game_type_code(_season(slug="preseason")) == 1
        assert game_type_code(_season(slug="regular-season")) == 2
        assert game_type_code(_season(slug="postseason")) == 3

    def test_game_type_falls_back_to_espn_type_then_zero(self) -> None:
        assert game_type_code(_season(slug=None, type_=3)) == 3
        assert game_type_code(_season(slug="summer-league", type_=None)) == 0
        assert game_type_code(None) == 0

    def test_score_line(self) -> None:
        assert _score_line(104, 117) == "104-117"
        assert _score_line(None, None) == "no score yet"
        assert _score_line(None, 5) == "?-5"


def _season(*, slug: str | None, type_: int | None = None):
    from models.nba_live import NbaSeason

    return NbaSeason(year=2027, type=type_, slug=slug)


class TestNbaTeamRowId:
    def test_espn_id_is_namespaced(self) -> None:
        assert nba_team_row_id(5) == NBA_TEAM_ID_OFFSET + 5
        assert nba_team_row_id(5) != nba_team_row_id(2)

    def test_out_of_range_ids_fail_loudly(self) -> None:
        # Guards double-offsetting and nonsense from a broken payload.
        with pytest.raises(ValueError, match="invalid ESPN team id"):
            nba_team_row_id(0)
        with pytest.raises(ValueError, match="invalid ESPN team id"):
            nba_team_row_id(-3)
        with pytest.raises(ValueError, match="invalid ESPN team id"):
            nba_team_row_id(NBA_TEAM_ID_OFFSET)


class TestBuildGame:
    def test_builds_nba_row_from_event(self) -> None:
        (game,) = parse(
            make_event(
                state="post",
                completed=True,
                detail="Final",
                away_score="104",
                home_score="117",
                date="2026-11-06T02:00Z",
            )
        )
        row = NbaPollWorker._build_game(game)
        assert row.league == "nba"
        assert row.game_id == 401909936
        assert row.away_team_id == NBA_TEAM_ID_OFFSET + 5
        assert row.home_team_id == NBA_TEAM_ID_OFFSET + 2
        assert row.season == 20262027
        assert row.game_type == 2
        assert row.game_state == "FINAL"
        assert row.away_team_score == 104
        assert row.home_team_score == 117
        assert row.start_time == datetime(2026, 11, 6, 2, 0, tzinfo=UTC)


class TestNbaLiveClient:
    def test_scoreboard_url_and_dates_param(self) -> None:
        http = _RecordingHttp()
        client = NbaLiveClient(base_url="https://example.test", http=http)

        asyncio.run(client.get_scoreboard("2026-11-06"))

        ((url, params),) = http.calls
        assert url.endswith("/apis/site/v2/sports/basketball/nba/scoreboard")
        assert params == {"dates": "20261106"}

    def test_malformed_day_fails_before_any_request(self) -> None:
        http = _RecordingHttp()
        client = NbaLiveClient(base_url="https://example.test", http=http)

        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            asyncio.run(client.get_scoreboard("11-06-2026"))

        assert http.calls == []


class _RecordingHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, *, params: dict | None = None, **kwargs):
        self.calls.append((url, params or {}))
        return SimpleNamespace(json=lambda: {"events": []})
