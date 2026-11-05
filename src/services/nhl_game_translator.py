"""Translate raw NHL game data (play-by-play, boxscore, ...) into the
app's domain concepts.

Pure — no DB, no API clients — so the poller and the text layout can
both use it and unit tests need no fixtures. ``NhlService`` takes an
instance as a constructor dependency (``NhlService(game_translator=...)``,
defaulting to a fresh one).

The NHL stamps every play in a play-by-play payload with a 4-digit
``situationCode``::

    [away goalie][away skaters][home skaters][home goalie]

Goalie digits are 1 (in net) or 0 (pulled); the skater digits count
the five-a-side skaters on the ice. So ``1551`` is 5-on-5, ``1541`` is
an away power play (home has 4 skaters), ``1531`` is a home 5-on-3,
and ``0651`` is the away net empty with an extra attacker. Verified
against real gamecenter payloads (2026020214/215/216): the code at
the faceoff after a penalty always reflects the strength that
penalty produced — including offsetting/coincidental minors, where
the feed itself decides (e.g. a fighting pair returning to ``1551``).

A penalty play's own code describes the state at the moment of the
infraction — during a delayed penalty it shows the non-offending
team's pulled goalie (``1560``) — so the resulting strength comes
from the next faceoff, not from the penalty play itself. The feed
already settles offsetting and coincidental minors there (a fighting
pair returns to ``1551``), which is why the faceoff — not "the next
code that differs" — is the anchor: the latter would run past an
offsetting penalty into an unrelated later state.

A *goal* play's own code is the opposite: it describes the state the
goal was scored in, so goal labels read straight off the goal play —
no faceoff lookup needed. Verified against the 2026-11-03 slate:
``1541`` on an away power-play goal (2026020236/444) and ``0651`` on
an empty-netter (2026020238/1106, the *scoring* side's digit still 1).
"""

from __future__ import annotations

from models.nhl import GameSituation, SkaterStrength


class NhlGameTranslator:
    """Translates the NHL API's game payloads into app domain concepts.

    The pydantic models in :mod:`models.nhl` handle the structural
    side (JSON aliases → field names); this class owns the semantic
    side — opaque feed vocabulary (``situationCode`` strings) →
    concepts the app reasons about (on-ice strength, tracked-team
    phrases). Stateless: one instance is injected into ``NhlService``
    and shared safely.
    """

    @staticmethod
    def parse_situation_code(code: str | None) -> SkaterStrength | None:
        """Skater counts from a 4-digit code; ``None`` for anything malformed.

        Wrong-length or non-digit codes return ``None`` rather than guess —
        a penalty post with no strength line beats a wrong one.
        """
        if code is None or len(code) != 4 or not code.isdigit():
            return None
        return SkaterStrength(away_skaters=int(code[1]), home_skaters=int(code[2]))

    @staticmethod
    def parse_situation_state(code: str | None) -> GameSituation | None:
        """Full state (goalies *and* skaters) from a 4-digit code.

        Same strictness as :meth:`parse_situation_code` — wrong length or
        non-digit input returns ``None`` rather than guessing: an unlabelled
        "goal!" headline beats a wrong one. Goalie digits are read as
        "in net" unless they are the feed's ``0`` (pulled).
        """
        if code is None or len(code) != 4 or not code.isdigit():
            return None
        return GameSituation(
            away_goalie_in_net=code[0] != "0",
            away_skaters=int(code[1]),
            home_skaters=int(code[2]),
            home_goalie_in_net=code[3] != "0",
        )

    @staticmethod
    def simulate_after_penalty(
        own_code: str | None, *, offending_is_home: bool
    ) -> SkaterStrength | None:
        """Estimated strength when the penalty is the newest play.

        The poller normally reads the settled state off the next faceoff;
        but the first poll that sees a penalty may see no play after it
        yet. This estimates from the penalty's own code: a pulled goalie
        (digit 0) is restored to a full five skaters — the extra attacker
        leaves the ice at the whistle — then the offending side loses one
        skater. So ``1551`` + away penalty → 5 on 4 for home, and the
        delayed-penalty look ``1560`` + away penalty settles the same way.

        ``None`` when ``own_code`` doesn't parse.
        """
        if own_code is None or len(own_code) != 4 or not own_code.isdigit():
            return None
        digits = list(own_code)
        for goalie_pos, skater_pos in ((0, 1), (3, 2)):
            if digits[goalie_pos] == "0":
                digits[goalie_pos] = "1"
                digits[skater_pos] = "5"
        skater_pos = 2 if offending_is_home else 1
        digits[skater_pos] = str(max(int(digits[skater_pos]) - 1, 0))
        return SkaterStrength(away_skaters=int(digits[1]), home_skaters=int(digits[2]))

    @staticmethod
    def strength_phrase(
        strength: SkaterStrength,
        *,
        tracked_team_id: int,
        home_team_id: int,
        away_team_id: int,
    ) -> str:
        """Tracked-team-facing phrase for a strength state.

        Power play / penalty kill when one side is a man up or down, the
        raw matchup otherwise ("4 on 4", "3 on 3"), "even strength" for a
        full 5-on-5 (offsetting minors) or an unresolvable side.
        """
        ours, theirs = strength.split_for(
            team_id=tracked_team_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
        )
        if ours < 0:
            return "even strength"
        if ours > theirs:
            return f"power play ({ours} on {theirs})"
        if ours < theirs:
            return f"penalty kill ({ours} on {theirs})"
        return "even strength" if ours == 5 else f"{ours} on {theirs}"

    @staticmethod
    def goal_situation_phrase(
        situation: GameSituation | None,
        *,
        tracked_team_id: int,
        home_team_id: int,
        away_team_id: int,
    ) -> str | None:
        """Headline label for a goal scored in ``situation`` (``None`` = plain).

        ``None`` means "no label": even strength — 5 on 5, 4 on 4 after
        offsetting minors, 3 on 3 in overtime — as well as anything
        unresolvable (unparseable code, a tracked team that is neither
        side of the matchup) all fall back to the plain ``#{TEAM} goal!``.
        The labels are "power play", "short handed", and "empty net",
        which the post builder renders as e.g. ``#CBJ power play goal!``.

        Order matters, and each branch is anchored on a real payload:

        1. **Empty net first** — the *opponent's* goalie digit is 0.
           Strength alone gets this backwards: a team trailing late pulls
           its goalie, so the leader's empty-netter arrives while they are
           being outskated 5 on 6 and would read "short handed"
           (2026020241 event 1038, code ``1560``). The mirror image
           exists too — the trailing scorer's *own* digit is 0
           (2026020237 events 1047/1063, code ``0651``), which is an
           extra-attacker goal, not an empty-net one.
        2. **Power play / short handed** — more skaters than the opponent,
           or fewer (2026020236 event 444, ``1541``: the away side scores
           on a home penalty kill).
        3. **Even strength** → ``None``.
        """
        if situation is None:
            return None
        if tracked_team_id == home_team_id:
            ours, theirs = situation.home_skaters, situation.away_skaters
            opponent_in_net = situation.away_goalie_in_net
        elif tracked_team_id == away_team_id:
            ours, theirs = situation.away_skaters, situation.home_skaters
            opponent_in_net = situation.home_goalie_in_net
        else:
            return None
        if not opponent_in_net:
            return "empty net"
        if ours > theirs:
            return "power play"
        if ours < theirs:
            return "short handed"
        return None
