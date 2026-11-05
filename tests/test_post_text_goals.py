"""Tests for the goal post layout in PostTextBuilder."""

from types import SimpleNamespace

from services.post_text import PostTextBuilder


def player(first: str, last: str, points: int) -> SimpleNamespace:
    return SimpleNamespace(first_name=first, last_name=last, assists=points)


def build(**overrides) -> str:
    kwargs = {
        "team_abbrev": "CBJ",
        "scorer": SimpleNamespace(first_name="Kirill", last_name="Marchenko"),
        "scorer_season_goals": 7,
        "assists": [player("Boone", "Jenner", 3)],
        "home_tri": "VAN",
        "home_score": 1,
        "away_tri": "CBJ",
        "away_score": 2,
        "home_shots": 18,
        "away_shots": 21,
        "time_remaining": "12:34",
        "period_number": 2,
    }
    kwargs.update(overrides)
    return PostTextBuilder.build_highlight_post_text(**kwargs)


class TestGoalHeadline:
    def test_even_strength_stays_plain(self) -> None:
        # 5 on 5 (and every other even-strength state) is just a goal.
        assert build(situation_phrase=None).splitlines()[0] == "#CBJ goal!"

    def test_no_situation_argument_stays_plain(self) -> None:
        assert build().splitlines()[0] == "#CBJ goal!"

    def test_empty_situation_degrades_to_plain(self) -> None:
        # Same degradation principle as a missing penalty situation.
        assert build(situation_phrase="").splitlines()[0] == "#CBJ goal!"

    def test_power_play_headline(self) -> None:
        text = build(situation_phrase="power play")
        assert text.splitlines()[0] == "#CBJ power play goal!"

    def test_short_handed_headline(self) -> None:
        text = build(situation_phrase="short handed")
        assert text.splitlines()[0] == "#CBJ short handed goal!"

    def test_empty_net_headline(self) -> None:
        text = build(situation_phrase="empty net")
        assert text.splitlines()[0] == "#CBJ empty net goal!"


class TestGoalPostLayout:
    def test_headline_scorer_assists_then_scoreboard(self) -> None:
        lines = build(situation_phrase="power play").splitlines()
        assert lines[0] == "#CBJ power play goal!"
        assert lines[1] == "🏒⚫ Kirill Marchenko (7)"
        assert lines[2] == "Assists: Boone Jenner (3)"
        # Blank line separates the header from the scoreboard, which
        # keeps its clock row and the aligned score/SOG rows.
        assert lines[3] == ""
        assert any("2nd" in line and "12:34" in line for line in lines)
        assert any(line == "VAN:  1 | CBJ:  2" for line in lines)
        assert any(line == "SOG: 18 | 21" for line in lines)

    def test_assists_line_dropped_when_nobody_assisted(self) -> None:
        lines = build(assists=[]).splitlines()
        assert lines[1] == "🏒⚫ Kirill Marchenko (7)"
        assert not any(line.startswith("Assists:") for line in lines)

    def test_unknown_scorer_is_not_invented(self) -> None:
        lines = build(scorer=None).splitlines()
        assert lines[1] == "🏒⚫ Unknown (7)"
