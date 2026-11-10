"""Tests for the quarter-end post layout in PostTextBuilder (NBA)."""

from services.post_text import PostTextBuilder

HOME_LEADERS = {"PTS": "Mitchell 23", "REB": "Hezonja 10", "AST": "Mitchell 8"}
AWAY_LEADERS = {"PTS": "Brown 20", "REB": "James 7", "AST": "Brown 5"}


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
        assert build().splitlines()[0] == "End of 1st quarter."
        assert build(period_number=2).splitlines()[0] == "End of 2nd quarter."
        assert build(period_number=3).splitlines()[0] == "End of 3rd quarter."
        assert build(period_number=4).splitlines()[0] == "End of 4th quarter."

    def test_missing_period_number(self) -> None:
        assert build(period_number=None).splitlines()[0] == "End of quarter."

    def test_period_past_regulation_is_overtime(self) -> None:
        # Basketball's regulation is 4 quarters: a 4 stays ordinal
        # (hockey's period_label would say OT), 5+ is overtime.
        assert build(period_number=5).splitlines()[0] == "End of overtime."


class TestQuarterEndScoreboard:
    def test_full_layout(self) -> None:
        lines = build().splitlines()
        assert lines[0] == "End of 1st quarter."
        assert lines[1] == ""
        # Plain rows (no dashed rules): four content rows wrapped in
        # a rule each ran ~370 chars, over Bluesky's 300-char cap.
        assert lines[2] == f"CLE: {'119':<11} | PHI: 95"
        assert lines[3] == f"PTS: {'Mitchell 23':<11} | Brown 20"
        assert lines[4] == f"REB: {'Hezonja 10':<11} | James 7"
        assert lines[5] == f"AST: {'Mitchell 8':<11} | Brown 5"
        assert len(lines) == 6

    def test_no_dashed_rule_lines(self) -> None:
        assert not any(set(line) == {"-"} for line in build().splitlines())

    def test_pipes_stack_across_rows(self) -> None:
        # One width scope: every "|" sits at the same column.
        rows = [line for line in build().splitlines() if "|" in line]
        assert len(rows) == 4
        assert len({line.index("|") for line in rows}) == 1

    def test_stays_under_bluesky_char_limit(self) -> None:
        # Regression guard: the earlier ruled layout was 371 chars and
        # the API rejects anything over 300. Longest realistic names
        # pad the left column the widest — still must fit.
        text = build(
            home_score=123,
            away_score=118,
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

    def test_without_leaders_only_the_scoreboard_shows(self) -> None:
        lines = build(home_leaders=None, away_leaders=None).splitlines()
        assert len(lines) == 3  # header, blank, score
        assert lines[2] == "CLE: 119 | PHI: 95"  # nothing wider to pad for

    def test_missing_scores_render_dash(self) -> None:
        lines = build(
            home_score=None,
            away_score=None,
            home_leaders=None,
            away_leaders=None,
        ).splitlines()
        assert lines[2] == "CLE: - | PHI: -"

    def test_missing_leader_cell_renders_dash(self) -> None:
        # Home has leaders, the away side doesn't: rows stay (both
        # teams keep their columns), the empty cells show "-".
        text = build(away_leaders={"PTS": None, "REB": None, "AST": None})
        assert f"PTS: {'Mitchell 23':<11} | -" in text
        assert " | PHI: " in text  # the score row keeps its label

    def test_all_empty_leader_cells_drop_the_rows(self) -> None:
        empty = {"PTS": None, "REB": None, "AST": None}
        lines = build(home_leaders=empty, away_leaders=empty).splitlines()
        assert len(lines) == 3
        assert lines[2] == "CLE: 119 | PHI: 95"

    def test_partial_leader_keys_from_one_side_only(self) -> None:
        # A side with no mapping at all still renders its column.
        text = build(home_leaders={"PTS": "Mitchell 23"}, away_leaders=None)
        assert f"PTS: {'Mitchell 23':<11} | -" in text
