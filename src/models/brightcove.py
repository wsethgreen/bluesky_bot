from pydantic import Field

from models.base import _Base


class BrightcoveSource(_Base):
    """One rendition. Progressive MP4s carry ``container == "MP4"``."""

    src: str | None = None
    type: str | None = None
    container: str | None = None
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    size: int | None = None
    avg_bitrate: int | None = None
    duration: int | None = None


class BrightcoveVideo(_Base):
    """Video object from the Playback API (metadata + renditions).

    Durations are in milliseconds. ``poster``/``thumbnail`` are flat URLs.
    """

    id: str | None = None
    account_id: str | None = None
    name: str | None = None
    description: str | None = None
    duration: int | None = None
    published_at: str | None = None
    updated_at: str | None = None
    poster: str | None = None
    thumbnail: str | None = None
    sources: list[BrightcoveSource] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
