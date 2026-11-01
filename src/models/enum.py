"""Shared enum types (no framework/DB dependencies)."""

from enum import StrEnum


class PostKind(StrEnum):
    """Kinds of Bluesky posts queued in the ``posts`` table.

    Each goal produces up to two rows: a ``text`` row claimed the moment
    the goal is detected, and a ``video`` row claimed once the NHL
    attaches the highlight clip id. A period-end play produces one
    ``period_end`` row: the "End of N period." scoreboard post, with
    the game-deciding one carrying the "Final: #CBJ Win!"/"Lose"
    result header instead. A ``penalty`` play produces one ``penalty``
    row: infraction, the tracked team's resulting strength (power play
    / penalty kill / 4 on 4), and a scoreboard.

    ``StrEnum`` so members bind and compare as their plain string
    values. The ``posts.kind`` column is a plain unconstrained string
    (the DB check constraint was dropped in migration a7d1c9e3f5b2),
    which makes this enum — not the database — the single source of
    valid values.
    """

    TEXT = "text"
    VIDEO = "video"
    PERIOD_END = "period_end"
    PENALTY = "penalty"
