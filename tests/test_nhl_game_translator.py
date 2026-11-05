"""Tests for NhlGameTranslator's situationCode decoding and phrasing.

The codes below are real ones observed in gamecenter payloads
(2026020214 NJD @ VGK, 2026020215 WPG @ FLA): ``1551`` is 5-on-5,
``1541`` a home penalty kill, ``1560`` the delayed-penalty look with
the home goalie pulled, and the faceoff after a call carries the
settled strength the penalty produced. Goal-play codes come from the
2026-11-03 slate (2026020236 UTA @ TOR, 2026020237 WPG @ MTL,
2026020238 DET @ TBL, 2026020241 MIN @ PHI).
"""

from models.nhl import GameSituation, SkaterStrength
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


class TestParseSituationState:
    def test_even_strength_keeps_both_goalies(self) -> None:
        assert translator.parse_situation_state("1551") == GameSituation(
            away_goalie_in_net=True,
            away_skaters=5,
            home_skaters=5,
            home_goalie_in_net=True,
        )

    def test_pulled_goalie_marks_only_that_side(self) -> None:
        # Real: an away empty net with the extra attacker on the ice.
        assert translator.parse_situation_state("0651") == GameSituation(
            away_goalie_in_net=False,
            away_skaters=6,
            home_skaters=5,
            home_goalie_in_net=True,
        )

    def test_delayed_penalty_look_marks_the_home_goalie_out(self) -> None:
        assert translator.parse_situation_state("1560") == GameSituation(
            away_goalie_in_net=True,
            away_skaters=5,
            home_skaters=6,
            home_goalie_in_net=False,
        )

    def test_malformed_codes_yield_none(self) -> None:
        assert translator.parse_situation_state(None) is None
        assert translator.parse_situation_state("") is None
        assert translator.parse_situation_state("155") is None
        assert translator.parse_situation_state("15511") is None
        assert translator.parse_situation_state("15a1") is None


class TestGoalSituationPhrase:
    def _phrase(self, code: str | None, tracked: int, *, home: int = 2, away: int = 1):
        return translator.goal_situation_phrase(
            translator.parse_situation_state(code),
            tracked_team_id=tracked,
            home_team_id=home,
            away_team_id=away,
        )

    def test_even_strength_is_unlabelled(self) -> None:
        assert self._phrase("1551", tracked=1) is None
        assert self._phrase("1551", tracked=2) is None

    def test_four_on_four_and_three_on_three_are_unlabelled(self) -> None:
        # Even strength is even strength: only special teams and an
        # empty net earn a word in the headline.
        assert self._phrase("1441", tracked=1) is None
        assert self._phrase("1331", tracked=2) is None

    def test_power_play_for_the_man_up_side(self) -> None:
        # Real: 2026020236 event 444 — away UTA scores while the home
        # side is down to four skaters on a penalty kill.
        assert self._phrase("1541", tracked=1) == "power play"

    def test_short_handed_for_the_man_down_side(self) -> None:
        # Same code read from the penalty-killing side's perspective.
        assert self._phrase("1541", tracked=2) == "short handed"

    def test_empty_net_beats_the_skater_count(self) -> None:
        # Real: 2026020241 event 1038 — MIN (away) scores into PHI's
        # empty net while PHI has the extra attacker, i.e. MIN is being
        # outskated 5 on 6. Skater counts alone would say "short
        # handed"; the opponent's goalie digit is what makes it an
        # empty-net goal.
        assert self._phrase("1560", tracked=1) == "empty net"

    def test_scoring_into_the_opponents_empty_net(self) -> None:
        # Real: 2026020238 event 1106 — TBL (home) into DET's net with
        # DET's goalie pulled, even though TBL is outskated 5 on 6.
        assert self._phrase("0651", tracked=2) == "empty net"

    def test_ones_own_pulled_goalie_is_not_an_empty_net_goal(self) -> None:
        # Real: 2026020237 event 1047 — trailing WPG (away) pulls its
        # own goalie and scores with the extra attacker. The puck went
        # into a net that had a goalie in it.
        assert self._phrase("0651", tracked=1) == "power play"

    def test_unknown_side_degrades_to_plain(self) -> None:
        assert self._phrase("1541", tracked=9) is None

    def test_unparseable_code_degrades_to_plain(self) -> None:
        assert self._phrase("15x1", tracked=1) is None
        assert (
            translator.goal_situation_phrase(
                None, tracked_team_id=1, home_team_id=2, away_team_id=1
            )
            is None
        )
