"""Tests for NhlGameTranslator's situationCode decoding and phrasing.

The codes below are real ones observed in gamecenter payloads
(2026020214 NJD @ VGK, 2026020215 WPG @ FLA): ``1551`` is 5-on-5,
``1541`` a home penalty kill, ``1560`` the delayed-penalty look with
the home goalie pulled, and the faceoff after a call carries the
settled strength the penalty produced.
"""

from models.nhl import SkaterStrength
from services.nhl_game_translator import NhlGameTranslator

translator = NhlGameTranslator()


class TestParseSituationCode:
    def test_even_strength(self) -> None:
        assert translator.parse_situation_code("1551") == SkaterStrength(
            away_skaters=5, home_skaters=5
        )

    def test_home_penalty_kill(self) -> None:
        assert translator.parse_situation_code("1541") == SkaterStrength(
            away_skaters=5, home_skaters=4
        )

    def test_home_five_on_three(self) -> None:
        assert translator.parse_situation_code("1531") == SkaterStrength(
            away_skaters=5, home_skaters=3
        )

    def test_pulled_goalie_keeps_extra_attacker_count(self) -> None:
        # Away goalie digit 0 (pulled); the skater digits still count.
        assert translator.parse_situation_code("0651") == SkaterStrength(
            away_skaters=6, home_skaters=5
        )

    def test_malformed_codes_yield_none(self) -> None:
        assert translator.parse_situation_code(None) is None
        assert translator.parse_situation_code("") is None
        assert translator.parse_situation_code("155") is None
        assert translator.parse_situation_code("15511") is None
        assert translator.parse_situation_code("15a1") is None


class TestSplitFor:
    STRENGTH = SkaterStrength(away_skaters=5, home_skaters=4)

    def test_home_side(self) -> None:
        assert self.STRENGTH.split_for(team_id=2, home_team_id=2, away_team_id=1) == (
            4,
            5,
        )

    def test_away_side(self) -> None:
        assert self.STRENGTH.split_for(team_id=1, home_team_id=2, away_team_id=1) == (
            5,
            4,
        )

    def test_neither_side(self) -> None:
        assert self.STRENGTH.split_for(team_id=9, home_team_id=2, away_team_id=1) == (
            -1,
            -1,
        )


class TestStrengthPhrase:
    def _phrase(self, strength: SkaterStrength, tracked: int) -> str:
        return translator.strength_phrase(
            strength, tracked_team_id=tracked, home_team_id=2, away_team_id=1
        )

    def test_power_play_for_man_up_side(self) -> None:
        # Away has 5, home 4: the away team is on the power play.
        assert (
            self._phrase(SkaterStrength(away_skaters=5, home_skaters=4), tracked=1)
            == "power play (5 on 4)"
        )

    def test_penalty_kill_for_man_down_side(self) -> None:
        assert (
            self._phrase(SkaterStrength(away_skaters=5, home_skaters=4), tracked=2)
            == "penalty kill (4 on 5)"
        )

    def test_even_strength_at_five_each(self) -> None:
        assert (
            self._phrase(SkaterStrength(away_skaters=5, home_skaters=5), tracked=2)
            == "even strength"
        )

    def test_four_on_four(self) -> None:
        assert (
            self._phrase(SkaterStrength(away_skaters=4, home_skaters=4), tracked=1)
            == "4 on 4"
        )

    def test_unknown_side_degrades_to_even_strength(self) -> None:
        assert (
            self._phrase(SkaterStrength(away_skaters=5, home_skaters=4), tracked=9)
            == "even strength"
        )


class TestSimulateAfterPenalty:
    def test_away_penalty_at_even_strength(self) -> None:
        # Real sequence: eventId 205 (VGK home minor) at 1551 → next
        # faceoff 1541; here the mirrored away-offender case.
        assert translator.simulate_after_penalty(
            "1551", offending_is_home=False
        ) == SkaterStrength(away_skaters=4, home_skaters=5)

    def test_home_penalty_at_even_strength(self) -> None:
        # Real sequence: eventId 205 (VGK home minor) at 1551 → the
        # next faceoff reads 1541 (home down to 4 skaters).
        assert translator.simulate_after_penalty(
            "1551", offending_is_home=True
        ) == SkaterStrength(away_skaters=5, home_skaters=4)

    def test_delayed_penalty_extra_attacker_settles(self) -> None:
        # Real sequence: eventId 367 (NJD away minor) with own code
        # 1560 — home had pulled the goalie for a delayed-penalty
        # extra attacker — settles at the next faceoff's 1451.
        assert translator.simulate_after_penalty(
            "1560", offending_is_home=False
        ) == SkaterStrength(away_skaters=4, home_skaters=5)

    def test_malformed_code_yields_none(self) -> None:
        assert translator.simulate_after_penalty(None, offending_is_home=True) is None
        assert translator.simulate_after_penalty("15x1", offending_is_home=True) is None
