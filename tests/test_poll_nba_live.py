"""Tests for the NBA poller: scoreboard parsing, state mapping, row state,
quarter detection, and summary parsing.

Payload shapes mirror ESPN's real payloads (verified against
``site.api.espn.com`` live data): string ids/scores, ``status.type``
state words, season objects whose ``year`` is the season *end*, and a
summary whose ``leaders`` are grouped per team. Everything under test
is pure — no DB, no network, no settings.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import Select
from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect

from clients.nba_live_client import NbaLiveClient
from db.models.game import Game
from db.models.team import NBA_TEAM_ID_OFFSET, nba_team_row_id
from jobs.poll_nba_live import (
    NbaPollWorker,
    _score_line,
    _stop_reason,
    completed_quarters,
    game_type_code,
    leader_cells,
    linescores_by_side,
    map_game_state,
    score_through,
    season_code,
    team_leaders,
)
from models.nba_live import NbaScheduleGame, NbaScoreboardResponse
from models.nba_summary import NbaSummaryLineScore, NbaSummaryResponse
from services.post_text import PostTextBuilder


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
    period: int | None = None,
    clock: float = 0.0,
) -> dict:
    """One real-shaped ESPN scoreboard event.

    ``period``/``clock`` shape the status the quarter-end gate reads:
    default to the historical fixture (0, except 4 once final), while
    e.g. ``state="in", period=2, clock=0`` is ESPN's halftime report.
    """

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

    status_period = period if period is not None else (4 if state == "post" else 0)
    return {
        "id": str(event_id),
        "date": date,
        "name": f"{away_abbrev} at {home_abbrev}",
        "shortName": f"{away_abbrev} @ {home_abbrev}",
        "season": {"year": season_year, "type": 2, "slug": season_slug},
        "status": {
            "clock": clock,
            "displayClock": "0:00",
            "period": status_period,
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
                    "clock": clock,
                    "displayClock": "0:00",
                    "period": status_period,
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

    def test_halftime_status_feeds_the_buzzer_gate(self) -> None:
        # ESPN's real halftime report: state still "in", period still
        # 2 (the quarter that just ended), clock at 0 — exactly the
        # shape the quarter-end gate reads as "Q2's buzzer sounded".
        event = make_event(state="in", detail="Halftime", period=2, clock=0)
        (game,) = parse(event)
        status = game.game_status
        assert status is not None
        assert status.period == 2
        assert status.clock == 0
        assert completed_quarters("LIVE", status.period, status.clock) == [1, 2]

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

    def test_summary_url_and_event_param(self) -> None:
        http = _RecordingHttp(payload=make_summary_payload())
        client = NbaLiveClient(base_url="https://example.test", http=http)

        summary = asyncio.run(client.get_summary(401909289))

        ((url, params),) = http.calls
        assert url.endswith("/apis/site/v2/sports/basketball/nba/summary")
        assert params == {"event": "401909289"}
        assert summary.leaders[0].team is not None
        assert summary.leaders[0].team.abbreviation == "CLE"


class TestCompletedQuarters:
    def test_final_counts_every_period_the_clock_reached(self) -> None:
        assert completed_quarters("FINAL", 4) == [1, 2, 3, 4]
        # An OT game's final clock reads 5: the OT period ended too.
        assert completed_quarters("FINAL", 5) == [1, 2, 3, 4, 5]

    def test_final_without_a_period_falls_back_to_regulation(self) -> None:
        assert completed_quarters("FINAL", None) == [1, 2, 3, 4]

    def test_live_counts_only_the_finished_quarters(self) -> None:
        assert completed_quarters("LIVE", 3) == [1, 2]
        assert completed_quarters("LIVE", 1) == []  # Q1 still running

    def test_live_buzzer_comes_from_the_clock_not_the_period(self) -> None:
        # ESPN holds period at the just-ended quarter through the
        # whole break (halftime: state=in, clock=0, period=2), so a
        # 0:00 clock is the buzzer — the post fires at halftime, not
        # when Q3 tips.
        assert completed_quarters("LIVE", 1, clock=0) == [1]
        assert completed_quarters("LIVE", 2, clock=0.0) == [1, 2]
        assert completed_quarters("LIVE", 3, clock=0) == [1, 2, 3]

    def test_live_running_clock_claims_only_the_past_quarters(self) -> None:
        assert completed_quarters("LIVE", 3, clock=32.3) == [1, 2]
        assert completed_quarters("LIVE", 1, clock=720) == []

    def test_live_buzzer_never_claims_the_deciding_period(self) -> None:
        # A 0:00 on period 4 or an OT period can mean the game is
        # over: the deciding quarter must wait for FINAL, whose claim
        # attaches the result header — a LIVE claim would freeze the
        # plain header under the dedup key forever.
        assert completed_quarters("LIVE", 4, clock=0) == [1, 2, 3]
        assert completed_quarters("LIVE", 5, clock=0) == [1, 2, 3, 4]

    def test_buzzer_gate_ignores_unplayed_states(self) -> None:
        assert completed_quarters("FUTURE", 2, clock=0) == []
        assert completed_quarters(None, 2, clock=0) == []
        assert completed_quarters("LIVE", 0, clock=0) == []

    def test_live_without_a_usable_period_claims_nothing(self) -> None:
        assert completed_quarters("LIVE", None) == []
        assert completed_quarters("LIVE", 0) == []

    def test_unplayed_or_unmappable_states_claim_nothing(self) -> None:
        assert completed_quarters("FUTURE", 0) == []
        assert completed_quarters("POSTPONED", None) == []
        assert completed_quarters("CANCELED", None) == []
        assert completed_quarters(None, 4) == []


class TestScoreThrough:
    @staticmethod
    def _line(value: str) -> NbaSummaryLineScore:
        return NbaSummaryLineScore.model_validate({"displayValue": value})

    def test_sums_through_the_quarter(self) -> None:
        lines = [self._line(v) for v in ("33", "29", "29", "28")]
        assert score_through(lines, 2) == 62

    def test_in_progress_later_quarter_does_not_leak_in(self) -> None:
        # Mid-Q3: the third entry is live. Through Q2 must ignore it.
        lines = [self._line(v) for v in ("22", "32", "7")]
        assert score_through(lines, 2) == 54

    def test_short_or_missing_data_is_none_not_zero(self) -> None:
        assert score_through(None, 1) is None
        assert score_through([], 1) is None
        assert score_through([self._line("22")], 2) is None

    def test_unparseable_entry_is_none(self) -> None:
        assert score_through([self._line("")], 1) is None

    def test_through_below_one_is_none(self) -> None:
        assert score_through([self._line("22")], 0) is None


def make_summary_payload() -> dict:
    """Real-shaped ESPN summary fragment (event 401909289, PHI @ CLE).

    Categories arrive points/assists/rebounds (the feed's order) — the
    post must still render PTS/REB/AST.
    """

    def team(
        abbrev: str,
        points: str,
        assists: str,
        rebounds: str,
        p_name: str,
        a_name: str,
        r_name: str,
    ) -> dict:
        return {
            "team": {"abbreviation": abbrev},
            "leaders": [
                {
                    "name": "points",
                    "leaders": [
                        {"displayValue": points, "athlete": {"displayName": p_name}}
                    ],
                },
                {
                    "name": "assists",
                    "leaders": [
                        {"displayValue": assists, "athlete": {"displayName": a_name}}
                    ],
                },
                {
                    "name": "rebounds",
                    "leaders": [
                        {"displayValue": rebounds, "athlete": {"displayName": r_name}}
                    ],
                },
            ],
        }

    return {
        "header": {
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "linescores": [
                                {"displayValue": v} for v in ("33", "29", "29", "28")
                            ],
                        },
                        {
                            "homeAway": "away",
                            "linescores": [
                                {"displayValue": v} for v in ("22", "32", "20", "21")
                            ],
                        },
                    ]
                }
            ]
        },
        "leaders": [
            team(
                "CLE",
                "23",
                "8",
                "10",
                "Donovan Mitchell",
                "Donovan Mitchell",
                "Mario Hezonja",
            ),
            team("PHI", "20", "5", "7", "Jaylen Brown", "Jaylen Brown", "LeBron James"),
        ],
    }


class TestTeamLeaders:
    def test_matches_tri_code_case_insensitively(self) -> None:
        summary = NbaSummaryResponse.model_validate(make_summary_payload())
        assert team_leaders(summary, "CLE") is not None
        assert team_leaders(summary, " cle ") is not None
        assert team_leaders(summary, "BOS") is None
        assert team_leaders(summary, None) is None
        assert team_leaders(summary, "") is None


class TestLeaderCells:
    def test_maps_espn_categories_to_post_labels_in_post_order(self) -> None:
        summary = NbaSummaryResponse.model_validate(make_summary_payload())
        cells = leader_cells(team_leaders(summary, "CLE"))
        assert list(cells) == ["PTS", "REB", "AST"]  # not the feed's order
        assert cells["PTS"] == "Donovan Mitchell 23"
        assert cells["REB"] == "Mario Hezonja 10"
        assert cells["AST"] == "Donovan Mitchell 8"

    def test_missing_category_is_none_not_a_dropped_row(self) -> None:
        summary = NbaSummaryResponse.model_validate(
            {
                "leaders": [
                    {
                        "team": {"abbreviation": "CLE"},
                        "leaders": [
                            {
                                "name": "points",
                                "leaders": [
                                    {
                                        "displayValue": "23",
                                        "athlete": {"displayName": "D. Mitchell"},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
        cells = leader_cells(team_leaders(summary, "CLE"))
        assert cells == {"PTS": "D. Mitchell 23", "REB": None, "AST": None}

    def test_no_team_leaders_is_all_none(self) -> None:
        empty = {"PTS": None, "REB": None, "AST": None}
        summary = NbaSummaryResponse.model_validate({"leaders": []})
        assert leader_cells(team_leaders(summary, "CLE")) == empty
        assert leader_cells(None) == empty

    def test_name_falls_back_to_full_name(self) -> None:
        summary = NbaSummaryResponse.model_validate(
            {
                "leaders": [
                    {
                        "team": {"abbreviation": "CLE"},
                        "leaders": [
                            {
                                "name": "points",
                                "leaders": [
                                    {
                                        "displayValue": "23",
                                        "athlete": {"fullName": "Donovan Mitchell"},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
        cells = leader_cells(team_leaders(summary, "CLE"))
        assert cells["PTS"] == "Donovan Mitchell 23"

    def test_leader_without_a_name_keeps_the_value(self) -> None:
        summary = NbaSummaryResponse.model_validate(
            {
                "leaders": [
                    {
                        "team": {"abbreviation": "CLE"},
                        "leaders": [
                            {
                                "name": "points",
                                "leaders": [{"displayValue": "23"}],
                            }
                        ],
                    }
                ]
            }
        )
        cells = leader_cells(team_leaders(summary, "CLE"))
        assert cells["PTS"] == "23"


class TestLinescoresBySide:
    def test_splits_home_and_away(self) -> None:
        summary = NbaSummaryResponse.model_validate(make_summary_payload())
        home, away = linescores_by_side(summary, 401909289)
        assert score_through(home, 1) == 33
        assert score_through(away, 1) == 22

    def test_missing_linescores_fail_loudly(self) -> None:
        # Claiming without the post's core content (the quarter score)
        # would freeze a "-" the dedup key could never re-claim.
        summary = NbaSummaryResponse.model_validate(
            {"header": {"competitions": [{"competitors": [{"homeAway": "home"}]}]}}
        )
        with pytest.raises(ValueError, match="per-quarter scores"):
            linescores_by_side(summary, 401909289)


class TestQuarterPostFromSummary:
    def test_end_of_first_quarter_post(self) -> None:
        # The whole path in miniature: summary → sides/leaders/cells →
        # text, exactly as _queue_quarter_ends assembles it.
        summary = NbaSummaryResponse.model_validate(make_summary_payload())
        home, away = linescores_by_side(summary, 401909289)
        text = PostTextBuilder.build_quarter_end_text(
            period_number=1,
            home_tri="CLE",
            home_score=score_through(home, 1),
            away_tri="PHI",
            away_score=score_through(away, 1),
            home_leaders=leader_cells(team_leaders(summary, "CLE")),
            away_leaders=leader_cells(team_leaders(summary, "PHI")),
            campaign_tag="LetEmKnow",
        )
        lines = text.splitlines()
        assert lines[0] == "End of the 1st quarter. #LetEmKnow"
        assert lines[2] == "CLE: 33  |  PHI: 22"
        # Blank line: the score row stands alone above the leaders.
        assert lines[3] == ""
        assert lines[4] == "PTS: Donovan Mitchell 23  |  Jaylen Brown 20"
        assert lines[5] == "REB: Mario Hezonja 10  |  LeBron James 7"
        assert lines[6] == "AST: Donovan Mitchell 8  |  Jaylen Brown 5"
        # The tag rides the header line — nothing trails the board.
        assert len(lines) == 7

    def test_final_quarter_uses_final_buzzer_scores(self) -> None:
        summary = NbaSummaryResponse.model_validate(make_summary_payload())
        home, away = linescores_by_side(summary, 401909289)
        text = PostTextBuilder.build_quarter_end_text(
            period_number=4,
            home_tri="CLE",
            home_score=score_through(home, 4),
            away_tri="PHI",
            away_score=score_through(away, 4),
        )
        lines = text.splitlines()
        assert lines[0] == "End of the 4th quarter."
        assert lines[2] == "CLE: 119  |  PHI: 95"


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


def claim_params(session: FakeSession) -> list[dict]:
    """Bound params of each executed insert (the claim calls)."""
    return [
        dict(stmt.compile(dialect=asyncpg_dialect.dialect()).params)
        for stmt in session.statements
        if not isinstance(stmt, Select)
    ]


class TestQueueQuarterEnds:
    """_queue_quarter_ends: dedup, and the game-over result header."""

    @staticmethod
    def make_worker(summary: NbaSummaryResponse | None = None) -> NbaPollWorker:
        # Bypass __init__: it builds real API clients from settings;
        # the queue path only needs the summary stub and the team.
        if summary is None:
            summary = NbaSummaryResponse.model_validate(make_summary_payload())

        async def get_summary(game_id: int):
            return summary

        worker = NbaPollWorker.__new__(NbaPollWorker)
        worker._team = "CLE"
        worker._nba = SimpleNamespace(get_summary=get_summary)  # type: ignore[assignment]
        return worker

    @staticmethod
    def final_event() -> NbaScheduleGame:
        # CLE at home, display name "City CLE" (make_event's fixture);
        # the summary's buzzer score is CLE 119, PHI 95.
        return parse(make_event(state="post", away_abbrev="PHI", home_abbrev="CLE"))[0]

    def test_final_game_result_header_on_deciding_quarter(self) -> None:
        worker = self.make_worker()
        session = FakeSession()

        claimed = asyncio.run(
            worker._queue_quarter_ends(
                session, 401909289, self.final_event(), [1, 2, 3, 4], final=True
            )
        )

        assert claimed == 4
        texts = [p["text"] for p in claim_params(session)]
        # Q1-Q3 keep the plain line — now with the context sentence
        # (Q1: CLE 33, PHI 22 → lead by 11); only the deciding
        # quarter announces the tracked team's result, with the
        # same-line score sentence naming the opponent. The campaign
        # tag rides every header line.
        assert texts[0].splitlines()[0] == (
            "End of the 1st quarter. The City CLE lead the City PHI by 11. #LetEmKnow"
        )
        assert texts[3].splitlines()[0] == (
            "City CLE win! The City CLE defeat the City PHI 119-95. #LetEmKnow"
        )
        assert texts[3].splitlines()[2].startswith("CLE: 119")
        assert texts[3].splitlines()[0].endswith(" #LetEmKnow")

    def test_no_campaign_tag_for_non_cavs_team(self) -> None:
        # #LetEmKnow is the Cavs tag: another team's game gets the
        # same text shape with no tag at all.
        worker = self.make_worker()
        worker._team = "BOS"
        session = FakeSession()
        event = parse(make_event(state="post", away_abbrev="PHI"))[0]

        asyncio.run(worker._queue_quarter_ends(session, 401909289, event, [1]))

        text = claim_params(session)[0]["text"]
        assert text.splitlines()[0] == (
            "End of the 1st quarter. The City BOS lead the City PHI by 11."
        )
        assert "LetEmKnow" not in text

    def test_deciding_quarter_stays_plain_while_not_final(self) -> None:
        # completed=[1..4] while the game is still open (LIVE into
        # OT): the Q4 post must not claim a result.
        worker = self.make_worker()
        session = FakeSession()

        asyncio.run(
            worker._queue_quarter_ends(
                session, 401909289, self.final_event(), [1, 2, 3, 4], final=False
            )
        )

        texts = [p["text"] for p in claim_params(session)]
        # Plain header (no result) — the context sentence still names
        # the margin: CLE 119, PHI 95 through Q4.
        assert texts[3].splitlines()[0] == (
            "End of the 4th quarter. The City CLE lead the City PHI by 24. #LetEmKnow"
        )

    def test_already_claimed_quarters_are_not_requeued(self) -> None:
        worker = self.make_worker()
        session = FakeSession(existing=(1, 2, 3))  # Q4 still missing

        claimed = asyncio.run(
            worker._queue_quarter_ends(
                session, 401909289, self.final_event(), [1, 2, 3, 4], final=True
            )
        )

        assert claimed == 1
        texts = [p["text"] for p in claim_params(session)]
        assert len(texts) == 1
        assert texts[0].startswith("City CLE win!")

    def test_unscored_quarter_defers_to_the_next_poll(self) -> None:
        # The buzzer poll can beat the summary's linescore update:
        # claiming Q2 now would freeze a "-" the dedup key could never
        # re-claim, so Q1 claims and Q2 waits for the next poll.
        payload = make_summary_payload()
        for side in payload["header"]["competitions"][0]["competitors"]:
            side["linescores"] = side["linescores"][:1]
        worker = self.make_worker(NbaSummaryResponse.model_validate(payload))
        session = FakeSession()

        claimed = asyncio.run(
            worker._queue_quarter_ends(
                session, 401909289, self.final_event(), [1, 2], final=False
            )
        )

        assert claimed == 1
        texts = [p["text"] for p in claim_params(session)]
        assert len(texts) == 1
        assert texts[0].splitlines()[0] == (
            "End of the 1st quarter. The City CLE lead the City PHI by 11. #LetEmKnow"
        )


class _RecordingHttp:
    def __init__(self, payload: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._payload = payload if payload is not None else {"events": []}

    async def get(self, url: str, *, params: dict | None = None, **kwargs):
        self.calls.append((url, params or {}))
        return SimpleNamespace(json=lambda: self._payload)
