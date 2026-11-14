"""Tests for the quarter-end post layout in PostTextBuilder (NBA)."""

from services.post_text import PostTextBuilder

HOME_LEADERS = {"PTS": "Mitchell 23", "REB": "Hezonja 10", "AST": "Mitchell 8"}
AWAY_LEADERS = {"PTS": "Brown 20", "REB": "James 7", "AST": "Brown 5"}
NAMES = {
    "tracked_full_name": "Cleveland Cavaliers",
    "opponent_full_name": "New York Knicks",
}


def build(**overrides) -> str:
    kwargs = {
        "period_number": 1,
        "home_tri": "CLE",
        "home_score": 119,
        "away_tri": "PHI",
        "away_score": 95,
        "home_leaders": HOME_LEADERS,
        "away_leaders": AWAY_LEADERS,
    }
    kwargs.update(overrides)
    return PostTextBuilder.build_quarter_end_text(**kwargs)


class TestQuarterEndHeader:
    def test_ordinal_quarter_headers(self) -> None:
        assert build().splitlines()[0] == "End of the 1st quarter."
        assert build(period_number=2).splitlines()[0] == "End of the 2nd quarter."
        assert build(period_number=3).splitlines()[0] == "End of the 3rd quarter."
        assert build(period_number=4).splitlines()[0] == "End of the 4th quarter."

    def test_missing_period_number(self) -> None:
        assert build(period_number=None).splitlines()[0] == "End of quarter."

    def test_period_past_regulation_is_overtime(self) -> None:
        # Basketball's regulation is 4 quarters: a 4 stays ordinal
        # (hockey's period_label would say OT), 5+ is overtime.
        assert build(period_number=5).splitlines()[0] == "End of overtime."


class TestQuarterEndScoreboard:
    def test_full_layout(self) -> None:
        lines = build().splitlines()
        assert lines[0] == "End of the 1st quarter."
        assert lines[1] == ""
        # Plain rows (no dashed rules): four content rows wrapped in
        # a rule each ran ~370 chars, over Bluesky's 300-char cap.
        assert lines[2] == "CLE: 119  |  PHI: 95"
        # Blank line: the score row stands alone above the leaders.
        assert lines[3] == ""
        assert lines[4] == "PTS: Mitchell 23  |  Brown 20"
        assert lines[5] == "REB: Hezonja 10  |  James 7"
        assert lines[6] == "AST: Mitchell 8  |  Brown 5"
        # The campaign tag trails as its own paragraph.
        assert lines[7] == ""
        assert lines[8] == "#LetEmKnow"
        assert len(lines) == 9

    def test_let_em_know_tag_always_present(self) -> None:
        # Every quarter update carries the tag — even with no leader
        # rows and no scores to render.
        for text in (
            build(),
            build(home_leaders=None, away_leaders=None),
            build(home_score=None, away_score=None),
        ):
            assert text.endswith("\n\n#LetEmKnow")

    def test_no_dashed_rule_lines(self) -> None:
        assert not any(set(line) == {"-"} for line in build().splitlines())

    def test_rows_use_two_spaces_around_pipe(self) -> None:
        # No column alignment: every row is "left  |  right" — two
        # spaces of breathing room instead of padded columns.
        rows = [line for line in build().splitlines() if "|" in line]
        assert len(rows) == 4
        for line in rows:
            assert line.count("|") == 1
            assert "  |  " in line

    def test_stays_under_bluesky_char_limit(self) -> None:
        # Regression guard: the earlier ruled layout was 371 chars and
        # the API rejects anything over 300. Long team names in the
        # header sentence plus the longest realistic player names
        # must still fit.
        text = build(
            home_score=123,
            away_score=118,
            tracked_score=118,
            opponent_score=123,
            **NAMES,
            home_leaders={
                "PTS": "Gilgeous-Alexander 31",
                "REB": "Mitchell 12",
                "AST": "Donovan Mitchell 9",
            },
            away_leaders={
                "PTS": "Tatum 40",
                "REB": "Brown 15",
                "AST": "White 10",
            },
        )
        assert len(text) <= 300
        # The game-over variant's header (result + score sentence)
        # runs a little longer — same cap.
        final = build(
            home_score=123,
            away_score=118,
            is_final=True,
            tracked_score=123,
            opponent_score=118,
            **NAMES,
            home_leaders={
                "PTS": "Gilgeous-Alexander 31",
                "REB": "Mitchell 12",
                "AST": "Donovan Mitchell 9",
            },
            away_leaders={
                "PTS": "Tatum 40",
                "REB": "Brown 15",
                "AST": "White 10",
            },
        )
        assert len(final) <= 300

    def test_without_leaders_only_the_scoreboard_shows(self) -> None:
        lines = build(home_leaders=None, away_leaders=None).splitlines()
        # header, blank, score, blank, tag.
        assert len(lines) == 5
        assert lines[2] == "CLE: 119  |  PHI: 95"
        assert lines[4] == "#LetEmKnow"

    def test_missing_scores_render_dash(self) -> None:
        lines = build(
            home_score=None,
            away_score=None,
            home_leaders=None,
            away_leaders=None,
        ).splitlines()
        assert lines[2] == "CLE: -  |  PHI: -"

    def test_missing_leader_cell_renders_dash(self) -> None:
        # Home has leaders, the away side doesn't: rows stay (both
        # teams keep their columns), the empty cells show "-".
        text = build(away_leaders={"PTS": None, "REB": None, "AST": None})
        assert "PTS: Mitchell 23  |  -" in text
        assert "  |  PHI: " in text  # the score row keeps its label

    def test_all_empty_leader_cells_drop_the_rows(self) -> None:
        empty = {"PTS": None, "REB": None, "AST": None}
        lines = build(home_leaders=empty, away_leaders=empty).splitlines()
        assert len(lines) == 5
        assert lines[2] == "CLE: 119  |  PHI: 95"

    def test_partial_leader_keys_from_one_side_only(self) -> None:
        # A side with no mapping at all still renders its column.
        text = build(home_leaders={"PTS": "Mitchell 23"}, away_leaders=None)
        assert "PTS: Mitchell 23  |  -" in text


class TestQuarterEndContextSentence:
    """The header sentence: who leads and by how much."""

    def test_trail_sentence_when_behind(self) -> None:
        text = build(**NAMES, tracked_score=29, opponent_score=33)
        assert text.splitlines()[0] == (
            "End of the 1st quarter. The Cleveland Cavaliers trail"
            " the New York Knicks by 4."
        )

    def test_lead_sentence_when_ahead(self) -> None:
        text = build(**NAMES, tracked_score=33, opponent_score=29)
        assert text.splitlines()[0] == (
            "End of the 1st quarter. The Cleveland Cavaliers lead"
            " the New York Knicks by 4."
        )

    def test_tie_gets_no_sentence(self) -> None:
        # A tied game has no leader to name — the plain line stands.
        text = build(**NAMES, tracked_score=110, opponent_score=110)
        assert text.splitlines()[0] == "End of the 1st quarter."

    def test_missing_opponent_name_gets_no_sentence(self) -> None:
        text = build(
            tracked_full_name="Cleveland Cavaliers",
            tracked_score=29,
            opponent_score=33,
        )
        assert text.splitlines()[0] == "End of the 1st quarter."

    def test_missing_scores_get_no_sentence(self) -> None:
        text = build(**NAMES, tracked_score=None, opponent_score=None)
        assert text.splitlines()[0] == "End of the 1st quarter."

    def test_sentence_appends_to_overtime_header(self) -> None:
        text = build(period_number=5, **NAMES, tracked_score=115, opponent_score=110)
        assert text.splitlines()[0] == (
            "End of overtime. The Cleveland Cavaliers lead the New York Knicks by 5."
        )

    def test_opponent_article_is_not_doubled(self) -> None:
        # A name that already carries "the" passes through unchanged.
        text = build(
            tracked_full_name="Cleveland Cavaliers",
            opponent_full_name="The Knicks",
            tracked_score=33,
            opponent_score=29,
        )
        assert text.splitlines()[0] == (
            "End of the 1st quarter. The Cleveland Cavaliers lead The Knicks by 4."
        )


class TestQuarterEndFinalHeader:
    """Game-over header: the deciding quarter announces the result."""

    def test_win_header_uses_tracked_team_name(self) -> None:
        text = build(
            is_final=True,
            tracked_score=115,
            opponent_score=107,
            **NAMES,
        )
        assert text.splitlines()[0] == (
            "Cleveland Cavaliers win! The Cleveland Cavaliers defeat"
            " the New York Knicks 115-107."
        )

    def test_lose_header(self) -> None:
        text = build(
            is_final=True,
            tracked_score=107,
            opponent_score=115,
            **NAMES,
        )
        assert text.splitlines()[0] == (
            "Cleveland Cavaliers lose. The Cleveland Cavaliers fall to"
            " the New York Knicks 107-115."
        )

    def test_result_sentence_skipped_without_opponent_name(self) -> None:
        text = build(
            is_final=True,
            tracked_full_name="Cleveland Cavaliers",
            tracked_score=115,
            opponent_score=107,
        )
        assert text.splitlines()[0] == "Cleveland Cavaliers win!"

    def test_tie_falls_back_to_plain_header(self) -> None:
        # A tie can't claim a result — the end-of-quarter line stands.
        text = build(
            is_final=True,
            tracked_full_name="Cleveland Cavaliers",
            tracked_score=110,
            opponent_score=110,
        )
        assert text.splitlines()[0] == "End of the 1st quarter."

    def test_missing_name_or_scores_fall_back(self) -> None:
        no_name = build(is_final=True, tracked_score=115, opponent_score=107)
        assert no_name.splitlines()[0] == "End of the 1st quarter."
        no_scores = build(is_final=True, tracked_full_name="Cleveland Cavaliers")
        assert no_scores.splitlines()[0] == "End of the 1st quarter."

    def test_not_final_keeps_plain_header(self) -> None:
        # Name + scores alone never announce a result mid-game — the
        # header stays an end-of-quarter line (context sentence here
        # only when the opponent's name is known too).
        text = build(
            tracked_full_name="Cleveland Cavaliers",
            tracked_score=115,
            opponent_score=107,
        )
        assert text.splitlines()[0] == "End of the 1st quarter."

    def test_final_ot_period_announces_result_not_overtime(self) -> None:
        # The deciding period may be an OT period: the result header
        # wins over "End of overtime.".
        text = build(
            period_number=5,
            is_final=True,
            tracked_full_name="Cleveland Cavaliers",
            tracked_score=115,
            opponent_score=107,
        )
        assert text.splitlines()[0] == "Cleveland Cavaliers win!"
