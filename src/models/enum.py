"""Shared enum types (no framework/DB dependencies)."""

from enum import StrEnum


class PostKind(StrEnum):
    """Kinds of Bluesky posts queued in the ``posts`` table.

    Each goal produces up to two rows: a ``text`` row claimed the moment
    the goal is detected, and a ``video`` row claimed once the NHL
    attaches the highlight clip id. A period-end play produces one
    ``period_end`` row: the "End of N period." scoreboard post.

    ``StrEnum`` so members bind and compare as their plain string values
    (the column is ``VARCHAR(20)`` with a check constraint, not a native
    Postgres enum type).
    """

    TEXT = "text"
    VIDEO = "video"
    PERIOD_END = "period_end"
