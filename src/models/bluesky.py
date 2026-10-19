"""Bluesky value objects shared by the client and workers."""

from pydantic import ConfigDict

from models.base import _Base


class BlueskyPostRef(_Base):
    """Strong ref (uri + cid) to a published post — enough to quote it.

    Frozen because a ref is a value, not a mutable handle. Redeclares the
    base config (pydantic replaces, not merges, an overridden
    ``model_config``) to add ``frozen=True``.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True, frozen=True)

    uri: str
    cid: str
