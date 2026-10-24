"""Bluesky post text formatting: goal posts, period-end scoreboards, and
highlight-video captions.

Pure formatting — no DB, no API clients — so the poll worker can stay
focused on discovery/persistence and the layout can be tweaked (or
unit-tested) in one place.
"""


def _ordinal(number: int) -> str:
    """Cardinal with English suffix: 1 → "1st", 4 → "4th", 11 → "11th".

    Separate from ``PostTextBuilder.period_label`` on purpose: that
    helper bakes in hockey's 3 regulation periods (4+ → "OT"), while
    period-end headers must render any regulation length — basketball's
    Q4 is a "4th", not an "OT".
    """
    if 10 < number % 100 < 14:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


class PostTextBuilder:
    """Builders for the texts the poster publishes.

    Stateless: every method is a ``@staticmethod``; instantiate-free
    usage (``PostTextBuilder.build_highlight_post_text(...)``) is the
    norm.
    """

    @staticmethod
    def period_label(period_number: int | None) -> str:
        """Period with ordinal suffix for 1/2/3, OT for 4+, empty if None.

        Only ever called for scoring plays, so period >= 4 means overtime
        (the shootout is period 5 but has no scoring events).
        """
        if period_number is None:
            return ""
        if period_number == 1:
            return "1st"
        if period_number == 2:
            return "2nd"
        if period_number == 3:
            return "3rd"
        if period_number >= 4:
            return "OT"
        return str(period_number)

    @staticmethod
    def scoreboard_lines(
        *,
        period_number: int | None,
        time_remaining: str | None = None,
        home_tri: str | None,
        home_score: int | None,
        away_tri: str | None,
        away_score: int | None,
        home_shots: int | None = None,
        away_shots: int | None = None,
        show_clock: bool = True,
    ) -> list[str]:
        """Minimal ASCII scoreboard (clock, score, shots).

        Dashed rules bracket every line; the only pipes are the inner
        dividers between the two teams' numbers, so nothing frames the
        content (corner pieces render badly on mobile anyway):

            -------------------
            3rd ⏱️ 12:34
            -------------------
            TOR:  1 | CBJ:  0
            -------------------
            SOG: 32 | 30
            -------------------

        Numbers are right-aligned in 2-wide fields (scores after the
        colon, shot counts after the "SOG:" label) so the inner pipes and
        the digits line up between rows. Rules span the widest row, and
        the whole thing widens naturally for pathological data.

        ``show_clock=False`` drops the clock row — for the period-end
        post the header already names the period, and the clock reads
        00:00 — leaving only score and shots bracketed by rules:

            -------------------
            TOR:  1 | CBJ:  0
            -------------------
            SOG: 32 | 30
            -------------------
        """
        rows: list[str] = []
        if show_clock:
            clock = time_remaining or "--:--"
            period_label = PostTextBuilder.period_label(period_number)
            # Stopwatch emoji instead of the old text separator: reads as
            # "time left" on a phone and costs no width in emoji fonts.
            rows.append(f"{period_label}  ⏱️  {clock}" if period_label else f"⏱️ {clock}")
        home_goals = home_score or 0
        away_goals = away_score or 0
        home_sog = home_shots or 0
        away_sog = away_shots or 0
        rows.append(
            f"{(home_tri or '?')}: {home_goals:>2} | {(away_tri or '?')}: {away_goals:>2}"
        )
        rows.append(f"SOG: {home_sog:>2} | {away_sog:>2}")

        bar = "-" * max(len(row) for row in rows)
        lines: list[str] = []
        for row in rows:
            lines.append(bar)
            lines.append(row)
        lines.append(bar)
        return lines

    @staticmethod
    def build_highlight_post_text(
        *,
        team_abbrev: str,
        scorer: object | None,
        scorer_season_goals: int,
        assists: list[object | None],
        home_tri: str | None,
        home_score: int | None,
        away_tri: str | None,
        away_score: int | None,
        home_shots: int | None = None,
        away_shots: int | None = None,
        time_remaining: str | None,
        period_number: int | None,
    ) -> str:
        """Build the Bluesky post text for a goal.

        Format:

            #{TEAM} goal!
            🏒⚫ {Scorer Name} ({season goals})
            Assists: {Assist1 Name} ({season assists}), {Assist2 Name} ({season assists})
            -------------------
            3rd ⏱️ 12:34
            -------------------
            TOR:  1 | CBJ:  0
            -------------------
            SOG: 32 | 30
            -------------------

        The leading ``#{TEAM}`` is a real hashtag: the poster detects the
        prefix and attaches the tag facet to it, so the post opens with a
        tappable ``#CBJ goal!``. The assists line is omitted when there are
        no assisting players. The header and the scoreboard are joined
        with a blank line between them, while each group keeps single
        line breaks internally.
        """
        scorer_name = (
            f"{scorer.first_name} {scorer.last_name}"
            if scorer is not None
            else "Unknown"
        )
        header = [
            f"#{team_abbrev} goal!",
            f"🏒⚫ {scorer_name} ({scorer_season_goals})",
        ]

        assist_players = [a for a in assists if a is not None][:2]
        if assist_players:
            assist_parts = [
                f"{p.first_name} {p.last_name} ({p.assists or 0})"
                for p in assist_players
            ]
            header.append(f"Assists: {', '.join(assist_parts)}")

        scoreboard = PostTextBuilder.scoreboard_lines(
            period_number=period_number,
            time_remaining=time_remaining,
            home_tri=home_tri,
            home_score=home_score,
            away_tri=away_tri,
            away_score=away_score,
            home_shots=home_shots,
            away_shots=away_shots,
        )

        return "\n\n".join(["\n".join(header), "\n".join(scoreboard)])

    @staticmethod
    def build_period_end_text(
        *,
        period_number: int | None,
        home_tri: str | None,
        home_score: int | None,
        away_tri: str | None,
        away_score: int | None,
        home_shots: int | None = None,
        away_shots: int | None = None,
        unit_word: str = "period",
        regulation_periods: int = 3,
    ) -> str:
        """Build the Bluesky post text for the end of a period.

        Format:

            End of 1st period.

            -------------------
            TOR:  1 | CBJ:  0
            -------------------
            SOG: 32 | 30
            -------------------

        The header and the scoreboard are joined with a blank line,
        mirroring the goal post layout; the scoreboard drops its clock
        row (``show_clock=False``) because the header already names the
        period and the clock reads 00:00 at the buzzer.

        ``unit_word`` and ``regulation_periods`` are what keep the
        trigger layer sport-generic: hockey fills them with the defaults
        ("period", 3), basketball would pass ``unit_word="quarter"``,
        ``regulation_periods=4``. Past regulation the header collapses
        to "End of overtime." either way (the shootout, hockey's period
        5, lands there too). A missing period number renders
        "End of period.".
        """
        if period_number is None:
            header = f"End of {unit_word}."
        elif period_number > regulation_periods:
            header = "End of overtime."
        else:
            header = f"End of {_ordinal(period_number)} {unit_word}."

        scoreboard = PostTextBuilder.scoreboard_lines(
            period_number=period_number,
            home_tri=home_tri,
            home_score=home_score,
            away_tri=away_tri,
            away_score=away_score,
            home_shots=home_shots,
            away_shots=away_shots,
            show_clock=False,
        )
        return "\n\n".join([header, "\n".join(scoreboard)])

    @staticmethod
    def build_highlight_video_caption() -> str:
        """Caption for the standalone highlight video post.

        Deliberately minimal: the goal details live on the (quoted) text
        post, so the video post only carries the camera emoji — the team
        hashtag is appended by the poster.
        """
        return "🎥 "
