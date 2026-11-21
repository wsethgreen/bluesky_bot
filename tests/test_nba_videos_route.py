"""GET /nba/videos — browsing the postable Cavaliers videos.

The feed comes from the NBA content API (team-site videos: game
highlights, pre/post-game pressers, media availability). These tests
drive the real app over ASGI with the NBA service stubbed, so routing,
query validation, and upstream-error mapping are covered without any
network.
"""

from __future__ import annotations

import asyncio

import httpx2
import pytest

from app import app
from models.nba_content import NbaContentVideoResponse
from routes.dependencies import get_nba_service
from services.nba_service import NbaService

# Real-shaped content API payload: one highlight, one post-game presser.
VIDEO_PAYLOAD = {
    "results": {
        "count": 2,
        "items": [
            {
                "id": 1159204,
                "title": "Cavs at Knicks | Game Highlights | 11.11.2026",
                "category": "Game Highlights,Gameday",
                "featuredImage": "https://example.test/highlights.jpg",
                "videoAssets": {
                    "960": {
                        "width": 960,
                        "height": 540,
                        "video": "https://example.test/highlights-960.mp4",
                        "duration": 180000,
                    },
                    "1280": {
                        "width": 1280,
                        "height": 720,
                        "video": "https://example.test/highlights-1280.mp4",
                        "duration": 180000,
                    },
                    "duration": 180000,
                },
                "taxonomy": {
                    "categories": {
                        "game-highlights": "Game Highlights",
                        "gameday": "Gameday",
                    }
                },
            },
            {
                "id": 1159201,
                "title": "Cavs at Knicks | Kenny Atkinson Post Game | 11.11.2026",
                "category": "Gameday,Media Availability",
                "videoAssets": {
                    "960": {
                        "width": 960,
                        "height": 540,
                        "video": "https://example.test/postgame-960.mp4",
                        "duration": 300000,
                    },
                    "duration": 300000,
                },
            },
        ],
        "total": 2,
    }
}


class StubNbaService:
    """Records the route's arguments; returns/raises what the test sets."""

    def __init__(self) -> None:
        self.items = NbaContentVideoResponse.model_validate(VIDEO_PAYLOAD).results.items
        self.error: Exception | None = None
        self.calls: list[dict] = []

    async def get_team_videos(
        self, *, count: int = 50, offset: int = 0, category: str | None = None
    ) -> list:
        self.calls.append({"count": count, "offset": offset, "category": category})
        if self.error is not None:
            raise self.error
        return self.items


@pytest.fixture
def stub() -> StubNbaService:
    service = StubNbaService()
    app.dependency_overrides[get_nba_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_nba_service, None)


async def _get(path: str = "/nba/videos", **params: object) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        return await client.get(path, params=params or None)


def _upstream_status_error(status: int = 500) -> httpx2.HTTPStatusError:
    request = httpx2.Request("GET", "https://content-api-prod.nba.com/x")
    response = httpx2.Response(status, request=request)
    return httpx2.HTTPStatusError("upstream boom", request=request, response=response)


class TestListTeamVideosEndpoint:
    def test_returns_the_teams_videos(self, stub: StubNbaService) -> None:
        response = asyncio.run(_get())
        assert response.status_code == 200
        titles = [item["title"] for item in response.json()]
        assert titles == [item.title for item in stub.items]

    def test_passes_filters_through_to_the_service(self, stub: StubNbaService) -> None:
        response = asyncio.run(_get(category="game-highlights", count=10, offset=20))
        assert response.status_code == 200
        assert stub.calls == [
            {"count": 10, "offset": 20, "category": "game-highlights"}
        ]

    def test_defaults_when_no_params_given(self, stub: StubNbaService) -> None:
        asyncio.run(_get())
        assert stub.calls == [{"count": 50, "offset": 0, "category": None}]

    def test_invalid_count_is_rejected_before_any_lookup(
        self, stub: StubNbaService
    ) -> None:
        response = asyncio.run(_get(count=0))
        assert response.status_code == 422
        assert stub.calls == []

    def test_invalid_offset_is_rejected_before_any_lookup(
        self, stub: StubNbaService
    ) -> None:
        response = asyncio.run(_get(offset=-1))
        assert response.status_code == 422
        assert stub.calls == []

    def test_response_keeps_pickable_mp4_renditions(self, stub: StubNbaService) -> None:
        response = asyncio.run(_get())
        highlights = response.json()[0]
        assert highlights["featuredImage"].endswith(".jpg")
        renditions = highlights["videoAssets"]["renditions"]
        assert renditions["1280"]["video"].endswith(".mp4")
        assert renditions["960"]["height"] == 540

    def test_argument_error_is_422(self, stub: StubNbaService) -> None:
        stub.error = ValueError("count must be positive, got 0")
        response = asyncio.run(_get())
        assert response.status_code == 422
        assert response.json()["detail"] == "count must be positive, got 0"

    def test_upstream_http_status_error_is_502(self, stub: StubNbaService) -> None:
        stub.error = _upstream_status_error(503)
        response = asyncio.run(_get())
        assert response.status_code == 502
        assert "503" in response.json()["detail"]

    def test_upstream_network_error_is_503(self, stub: StubNbaService) -> None:
        stub.error = httpx2.HTTPError("connection refused")
        response = asyncio.run(_get())
        assert response.status_code == 503
        assert "connection refused" in response.json()["detail"]


class StubContentClient:
    """Just enough NbaContentClient for the service method under test."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get_latest_videos(
        self,
        *,
        team_id=None,
        count: int = 12,
        offset: int = 0,
        term_category=None,
    ) -> NbaContentVideoResponse:
        self.calls.append(
            {
                "count": count,
                "offset": offset,
                "term_category": term_category,
            }
        )
        return NbaContentVideoResponse.model_validate(VIDEO_PAYLOAD)


class TestNbaServiceTeamVideos:
    def test_passes_category_slug_to_the_content_client(self) -> None:
        # Bypass __init__: it builds real API clients from config, and
        # the method under test only needs the stub content client.
        service = NbaService.__new__(NbaService)
        content = StubContentClient()
        service.content = content  # type: ignore[assignment]

        items = asyncio.run(
            service.get_team_videos(count=25, offset=5, category="game-highlights")
        )

        assert content.calls == [
            {"count": 25, "offset": 5, "term_category": "game-highlights"}
        ]
        assert [video.id for video in items] == [1159204, 1159201]

    def test_no_category_returns_every_video(self) -> None:
        service = NbaService.__new__(NbaService)
        content = StubContentClient()
        service.content = content  # type: ignore[assignment]

        asyncio.run(service.get_team_videos())

        assert content.calls == [{"count": 50, "offset": 0, "term_category": None}]
