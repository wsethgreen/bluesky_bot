"""Client for the NHL Stats API (no key required)."""

from __future__ import annotations

from clients.http_client import HttpClient
from models.nhl_stats import NhlStatsTeam, NhlStatsTeamResponse

DEFAULT_BASE_URL = "https://api.nhle.com/stats/rest"


class NhlStatsClient:
    """Thin wrapper around ``api.nhle.com/stats/rest``.

    Separate from :class:`NhlClient` (one client per domain): different host,
    different path conventions (``/{lang}/...``), different response envelope
    (``{"data": [...], "total": n}``).
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self.http: HttpClient = HttpClient()
        self.base_url = base_url.rstrip("/")

    async def get_team(self, team_id: int) -> NhlStatsTeam | None:
        """Team directory entry by numeric id.

        ``GET /en/team/id/{team-id}`` — ``None`` when the API returns no rows.
        """
        if team_id <= 0:
            raise ValueError(f"team_id must be positive, got {team_id!r}")
        response = await self.http.get(f"{self.base_url}/en/team/id/{team_id}")
        teams = NhlStatsTeamResponse.model_validate(response.json()).data
        return teams[0] if teams else None
