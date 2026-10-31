"""Models for the NBA content API (team video feed).

See ``clients/nba_content_client.py``. The videos carry plain, DRM-free
MP4 URLs (``videoAssets``), unlike the League Pass watch API.
"""

from pydantic import Field, model_validator

from models.base import _Base


class NbaVideoRendition(_Base):
    """One MP4 rendition (keyed by width in ``videoAssets``: 320/960/1280)."""

    width: int
    height: int
    video: str
    thumbnail: str | None = None
    duration: int = Field(description="Duration in milliseconds")


class NbaVideoCaptions(_Base):
    """Caption files under ``videoAssets.cc``."""

    srt: str | None = None
    vtt: str | None = None


class NbaVideoAssets(_Base):
    """The mixed ``videoAssets`` bag: rendition widths + ``cc`` + ``duration``.

    The raw JSON keys the renditions by width alongside non-rendition keys
    (``cc``, ``duration``); the before-validator splits them apart.
    """

    renditions: dict[str, NbaVideoRendition] = Field(default_factory=dict)
    captions: NbaVideoCaptions | None = None
    duration: int | None = Field(default=None, description="Duration in milliseconds")

    @model_validator(mode="before")
    @classmethod
    def _split_assets(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        return {
            "renditions": {
                k: v for k, v in raw.items() if isinstance(v, dict) and "video" in v
            },
            "captions": raw.get("cc"),
            "duration": raw.get("duration"),
        }

    def best_rendition(self, max_height: int | None = None) -> NbaVideoRendition | None:
        """Tallest rendition, optionally capped at ``max_height``."""
        candidates = [
            rendition
            for rendition in self.renditions.values()
            if max_height is None or rendition.height <= max_height
        ]
        return max(candidates, key=lambda r: r.height, default=None)


class NbaVideoTaxonomy(_Base):
    """``taxonomy`` maps ids to display names for related entities."""

    categories: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    games: dict[str, str] = Field(default_factory=dict)
    teams: dict[str, str] = Field(default_factory=dict)
    players: dict[str, str] = Field(default_factory=dict)


class NbaContentVideo(_Base):
    """One team-site video (game highlights, media availability, etc.)."""

    id: int
    title: str
    slug: str | None = None
    permalink: str | None = None
    excerpt: str | None = None
    description: str | None = None
    category: str | None = None
    date: str | None = None
    modified: str | None = None
    timestamp: int | None = Field(default=None, description="Publish time, epoch ms")
    featured_image: str | None = Field(default=None, alias="featuredImage")
    video_duration_seconds: int | None = Field(
        default=None, alias="videoDurationSeconds"
    )
    video_creation_source: str | None = Field(
        default=None, alias="videoCreationSource", description="'wsc-feed' or 'manual'"
    )
    video_assets: NbaVideoAssets | None = Field(default=None, alias="videoAssets")
    taxonomy: NbaVideoTaxonomy = Field(default_factory=NbaVideoTaxonomy)


class NbaContentVideoResults(_Base):
    """``results`` envelope: a page of videos plus paging info.

    Note: ``total`` caps at 10000 and ``page_next`` is unreliable (the API
    often returns a constant), so paging should advance ``offset`` by the
    number of items received and stop on a short/empty page.
    """

    count: int = 0
    items: list[NbaContentVideo] = Field(default_factory=list)
    total: int = 0
    page_next: int | None = Field(default=None, alias="pageNext")
    pages: int | None = None


class NbaContentVideoResponse(_Base):
    results: NbaContentVideoResults
