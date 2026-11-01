"""Tests for the period-end / final-result post layout in PostTextBuilder."""

from services.post_text import PostTextBuilder


def build(**overrides) -> str:
    kwargs = {
        "period_number": 1,
        "home_tri": "TOR",
        "home_score": 1,
        "away_tri": "CBJ",
        "away_score": 0,
        "home_shots": 32,
        "away_shots": 30,
    }
    kwargs.update(overrides)
    return PostTextBuilder.build_period_end_text(**kwargs)


class TestPeriodEndPostText:
    def test_regular_period_header_and_scoreboard(self) -> None:
        text = build()
        lines = text.splitlines()
        assert lines[0] == "End of 1st period."
        # Blank line, then the clockless scoreboard.
        assert lines[1] == ""
        assert lines[2] == "-" * len(lines[2])
        assert any(line == "TOR:  1 | CBJ:  0" for line in lines)
        assert any(line == "SOG: 32 | 30" for line in lines)
        assert not any("⏱️" in line for line in lines)

    def test_missing_period_number(self) -> None:
        assert build(period_number=None).startswith("End of period.")

    def test_overtime_period(self) -> None:
        # Period 4+ collapses to overtime regardless of final-ness.
        assert build(period_number=4).startswith("End of overtime.")

    def test_final_flag_alone_changes_nothing(self) -> None:
        # is_final without scores/team can't claim a result.
        text = build(period_number=4, is_final=True)
        assert text.splitlines()[0] == "End of overtime."


class TestFinalResultHeader:
    def test_win_keeps_scoreboard(self) -> None:
        # CBJ (away) wins 2-1 in OT.
        text = build(
            period_number=4,
            home_score=1,
            away_score=2,
            team_abbrev="CBJ",
            is_final=True,
            tracked_score=2,
            opponent_score=1,
        )
        lines = text.splitlines()
        assert lines[0] == "Final: #CBJ Win! 💥"
        assert lines[1] == ""
        assert any(line == "TOR:  1 | CBJ:  2" for line in lines)
        assert any(line == "SOG: 32 | 30" for line in lines)

    def test_lose(self) -> None:
        text = build(
            period_number=3,
            team_abbrev="CBJ",
            is_final=True,
            tracked_score=1,
            opponent_score=3,
        )
        assert text.splitlines()[0] == "Final: #CBJ Lose"

    def test_tied_score_cannot_claim_a_result(self) -> None:
        text = build(
            team_abbrev="CBJ", is_final=True, tracked_score=2, opponent_score=2
        )
        assert text.splitlines()[0] == "End of 1st period."

    def test_missing_final_score_cannot_claim_a_result(self) -> None:
        text = build(
            team_abbrev="CBJ", is_final=True, tracked_score=None, opponent_score=3
        )
        assert text.splitlines()[0] == "End of 1st period."

    def test_missing_team_abbrev_cannot_claim_a_result(self) -> None:
        text = build(is_final=True, tracked_score=2, opponent_score=1)
        assert text.splitlines()[0] == "End of 1st period."

    def test_not_final_keeps_plain_header_even_with_decisive_scores(self) -> None:
        # End of the 3rd with a lead, but the game isn't over yet.
        text = build(
            period_number=3,
            team_abbrev="CBJ",
            is_final=False,
            tracked_score=2,
            opponent_score=1,
        )
        assert text.splitlines()[0] == "End of 3rd period."
