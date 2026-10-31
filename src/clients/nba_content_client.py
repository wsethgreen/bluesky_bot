"""Client for the NBA content API (team-site videos, no key required)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from clients.http_client import HttpClient
from config.settings import get_settings
from models.nba_content import NbaContentVideo, NbaContentVideoResponse

# The API hangs on non-browser User-Agents (e.g. HttpClient's default
# "bluesky-bot/0.1.0"), so send a browser-like one.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class NbaContentClient:
    """Thin wrapper around ``content-api-prod.nba.com``.

    ``GET /public/1/leagues/nba/teams/{team-id}/content/video`` serves the
    team-site video feed (game highlights, media availability, team misc).
    Unlike the League Pass watch API, these come with plain, DRM-free MP4
    URLs in ``videoAssets`` (see :meth:`get_latest_videos`).

    ``base_url`` and ``team_id`` default to the ``nba_content_base_url`` /
    ``nba_team_id`` settings; pass them explicitly to override (e.g. to
    follow a different team without changing env).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        team_id: int | str | None = None,
        http: HttpClient | None = None,
    ) -> None:
        settings = get_settings()
        # The content API stalls requests carrying a non-browser User-Agent
        # (ReadTimeout); it answers a browser UA in ~150ms.
        self.http = http or HttpClient(headers={"User-Agent": BROWSER_USER_AGENT})
        self.base_url = (base_url or settings.nba_content_base_url).rstrip("/")
        self.team_id = int(team_id or settings.nba_team_id)

    async def get_latest_videos(
        self,
        *,
        team_id: int | str | None = None,
        count: int = 12,
        offset: int = 0,
        term_category: int | None = None,
    ) -> NbaContentVideoResponse:
        """One page of team videos, newest first.

        ``GET /public/1/leagues/nba/teams/{team-id}/content/video`` with
        ``platform=web``. The API's own frontend sends ``count=undefined``
        (a bug — it returns 12); pass a real number to page properly.
        ``offset`` is in items, not pages. ``total`` caps at 10000.

        Args:
            team_id: Team to fetch for; defaults to ``self.team_id``.
            count: Page size (items).
            offset: Items to skip.
            term_category: Optional taxonomy term id to filter by
                (e.g. 1728 = "Gameday"); None for all team videos.

        Raises:
            ValueError: On negative count/offset or non-positive team_id.
        """
        if count <= 0:
            raise ValueError(f"count must be positive, got {count!r}")
        if offset < 0:
            raise ValueError(f"offset must be >= 0, got {offset!r}")
        resolved_team = int(team_id or self.team_id)
        if resolved_team <= 0:
            raise ValueError(f"team_id must be positive, got {team_id!r}")

        params: dict[str, Any] = {"platform": "web", "count": count, "offset": offset}
        if term_category is not None:
            params["term-category"] = term_category

        response = await self.http.get(
            f"{self.base_url}/public/1/leagues/nba/teams/{resolved_team}/content/video",
            params=params,
        )
        return NbaContentVideoResponse.model_validate(response.json())

    async def iter_latest_videos(
        self,
        *,
        team_id: int | str | None = None,
        count: int = 50,
        term_category: int | None = None,
        max_items: int | None = None,
    ) -> AsyncIterator[NbaContentVideo]:
        """Yield videos newest-first, paging until exhausted or ``max_items``.

        Advances ``offset`` by items received and stops on a short or empty
        page (``page_next`` in the response is unreliable).
        """
        if max_items is not None and max_items <= 0:
            return
        offset = 0
        yielded = 0
        while True:
            page = await self.get_latest_videos(
                team_id=team_id, count=count, offset=offset, term_category=term_category
            )
            items = page.results.items
            if not items:
                return
            for item in items:
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            if len(items) < count:
                return
            offset += len(items)
