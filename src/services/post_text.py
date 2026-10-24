"""Bluesky post text formatting: goal posts and highlight-video captions.

Pure formatting — no DB, no API clients — so the poll worker can stay
focused on discovery/persistence and the layout can be tweaked (or
unit-tested) in one place.
"""


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
        time_remaining: str | None,
        home_tri: str | None,
        home_score: int | None,
        away_tri: str | None,
        away_score: int | None,
        home_shots: int | None = None,
        away_shots: int | None = None,
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
        """
        clock = time_remaining or "--:--"
        period_label = PostTextBuilder.period_label(period_number)
        # Stopwatch emoji instead of the old text separator: reads as "time
        # left" on a phone and costs no width in emoji fonts.
        row_clock = f"{period_label}  ⏱️  {clock}" if period_label else f"⏱️ {clock}"
        home_goals = home_score or 0
        away_goals = away_score or 0
        home_sog = home_shots or 0
        away_sog = away_shots or 0
        row_score = f"{(home_tri or '?')}: {home_goals:>2} | {(away_tri or '?')}: {away_goals:>2}"
        row_shots = f"SOG: {home_sog:>2} | {away_sog:>2}"

        bar = "-" * max(len(row_clock), len(row_score), len(row_shots))
        return [
            bar,
            row_clock,
            bar,
            row_score,
            bar,
            row_shots,
            bar,
        ]

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
    def build_highlight_video_caption() -> str:
        """Caption for the standalone highlight video post.

        Deliberately minimal: the goal details live on the (quoted) text
        post, so the video post only carries the camera emoji — the team
        hashtag is appended by the poster.
        """
        return "🎥 "
