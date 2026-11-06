"""Client for the NBA scoreboard feed (ESPN site API, browser UA)."""

from __future__ import annotations

from clients.http_client import BROWSER_USER_AGENT, HttpClient
from config.settings import get_settings
from models.nba_live import NbaScoreboardResponse
from utils.date import normalize_date


class NbaLiveClient:
    """Scoreboard source for the NBA poller.

    ``GET /apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD``
    returns that (Eastern-time) day's games with status, scores, and
    team entries — one call covers both discovery and live state.

    ESPN rather than the NBA's official CDN: ``cdn.nba.com``'s liveData
    feed blocks non-browser clients outright (Akamai 403 regardless of
    User-Agent), while ESPN needs no key, answers a browser UA, and —
    unlike the today-only official feed — serves any date.

    ``base_url`` defaults to the ``nba.site_api_base_url`` setting
    (read lazily, so passing ``base_url`` explicitly needs no settings);
    ``http`` defaults to a browser-UA :class:`HttpClient` (ESPN 403s
    non-browser User-Agents).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        http: HttpClient | None = None,
    ) -> None:
        if base_url is None:
            base_url = get_settings().nba.site_api_base_url
        self.http = http or HttpClient(headers={"User-Agent": BROWSER_USER_AGENT})
        self.base_url = base_url.rstrip("/")

    async def get_scoreboard(self, day: str) -> NbaScoreboardResponse:
        """The games scheduled for ``day`` (YYYY-MM-DD, Eastern).

        Raises:
            ValueError: On a malformed ``day``.
            httpx.HTTPError: On transport / non-retryable HTTP failures.
        """
        day_iso = normalize_date(day)
        resp = await self.http.get(
            f"{self.base_url}/apis/site/v2/sports/basketball/nba/scoreboard",
            params={"dates": day_iso.replace("-", "")},
        )
        return NbaScoreboardResponse.model_validate(resp.json())
