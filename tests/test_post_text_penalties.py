"""Tests for the penalty post layout in PostTextBuilder."""

from services.post_text import PostTextBuilder


def build(**overrides) -> str:
    kwargs = {
        "team_abbrev": "CBJ",
        "situation_phrase": "power play (5 on 4)",
        "tracked_full_name": "Columbus Blue Jackets",
        "opponent_tri": "VAN",
        "infraction": "tripping",
        "offender_name": "Elias Pettersson",
        "period_number": 2,
        "time_remaining": "12:34",
        "home_tri": "VAN",
        "home_score": 1,
        "away_tri": "CBJ",
        "away_score": 2,
        "home_shots": 18,
        "away_shots": 21,
    }
    kwargs.update(overrides)
    return PostTextBuilder.build_penalty_post_text(**kwargs)


class TestPenaltyPostText:
    def test_power_play_headline_shouts_for_cbj(self) -> None:
        # The CBJ special case: nickname + shouty treatment + emoji.
        text = build()
        lines = text.splitlines()
        assert lines[0] == "#CBJ Jackets on the Powerrrrr Playyyyy! 🗣️"
        assert lines[1] == "Penalty against VAN — tripping (Elias Pettersson)."
        # Blank line, then the scoreboard with its clock row.
        assert lines[2] == ""
        assert lines[3] == "-" * len(lines[3])
        assert any("2nd" in line and "12:34" in line for line in lines)
        assert any(line == "VAN:  1 | CBJ:  2" for line in lines)
        assert any(line == "SOG: 18 | 21" for line in lines)

    def test_power_play_headline_shouts_cbj_even_without_full_name(self) -> None:
        # The CBJ check rides on the abbrev, so a missing name can't
        # lose fans their treatment.
        text = build(tracked_full_name=None)
        assert text.splitlines()[0] == "#CBJ Jackets on the Powerrrrr Playyyyy! 🗣️"

    def test_power_play_headline_uses_full_name_for_other_teams(self) -> None:
        text = build(team_abbrev="TBL", tracked_full_name="Tampa Bay Lightning")
        assert (
            text.splitlines()[0]
            == "#TBL Tampa Bay Lightning on the Powerrrrr Playyyyy! 🗣️"
        )

    def test_power_play_without_team_name_falls_back_to_plain_headline(self) -> None:
        # No name to shout (and not CBJ) → the plain phrase headline.
        text = build(team_abbrev="TBL", tracked_full_name=None)
        assert text.splitlines()[0] == "#TBL power play (5 on 4) ⚡"

    def test_penalty_kill_phrase(self) -> None:
        text = build(situation_phrase="penalty kill (4 on 5)")
        assert text.startswith("#CBJ penalty kill (4 on 5) ⚡")

    def test_even_strength_phrase(self) -> None:
        # Offsetting minors: still posted, situation says even strength.
        text = build(situation_phrase="even strength")
        assert text.startswith("#CBJ even strength ⚡")

    def test_missing_situation_degrades_to_penalty_headline(self) -> None:
        text = build(situation_phrase=None)
        assert text.startswith("#CBJ penalty ⚡")

    def test_missing_infraction_and_player(self) -> None:
        text = build(infraction=None, offender_name=None)
        assert "Penalty against VAN." in text.splitlines()[1]

    def test_missing_player_keeps_infraction(self) -> None:
        text = build(offender_name=None)
        assert text.splitlines()[1] == "Penalty against VAN — tripping."

    def test_missing_opponent_tri_still_renders(self) -> None:
        text = build(opponent_tri=None)
        assert text.splitlines()[1].startswith("Penalty against ?")
