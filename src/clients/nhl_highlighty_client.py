"""Client for the Highlightly NHL & NCAAH API (player/game highlights)."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from clients.http_client import HttpClient
from models.highlighty import HighlightlyHighlightsResponse

DEFAULT_BASE_URL = "https://nhl.highlightly.net"


class NhlHighlightyClient:
    """Thin wrapper around ``GET /highlights``.

    The shared :class:`HttpClient` is injected; this client only adds the
    Highlightly base URL, auth header, and response parsing.
    """

    def __init__(
        self,
        http: HttpClient,
        *,
        api_key: str | SecretStr,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if isinstance(api_key, SecretStr):
            api_key = api_key.get_secret_value()
        if not api_key:
            raise ValueError("Highlightly api_key must not be empty")
        self.http = http
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        # Header is named x-rapidapi-key even on direct Highlightly calls.
        return {"x-rapidapi-key": self.api_key}

    async def get_highlights(
        self,
        *,
        league_name: str | None = None,
        date: str | None = None,
        timezone: str | None = None,
        season: int | None = None,
        match_id: int | None = None,
        home_team_id: int | None = None,
        away_team_id: int | None = None,
        home_team_abbreviation: str | None = None,
        away_team_abbreviation: str | None = None,
        limit: int = 40,
        offset: int = 0,
        extra_params: dict[str, Any] | None = None,
    ) -> HighlightlyHighlightsResponse:
        """Fetch highlights. At least one primary filter is required.

        Primary filters: league_name, date, season, match_id, team ids or
        abbreviations. ``timezone``/``limit``/``offset`` alone do not count
        (per Highlightly docs) and raise ``ValueError``.
        """
        params: dict[str, Any] = {}
        if league_name is not None:
            params["leagueName"] = league_name
        if date is not None:
            params["date"] = date
        if timezone is not None:
            params["timezone"] = timezone
        if season is not None:
            params["season"] = season
        if match_id is not None:
            params["matchId"] = match_id
        if home_team_id is not None:
            params["homeTeamId"] = home_team_id
        if away_team_id is not None:
            params["awayTeamId"] = away_team_id
        if home_team_abbreviation is not None:
            params["homeTeamAbbreviation"] = home_team_abbreviation
        if away_team_abbreviation is not None:
            params["awayTeamAbbreviation"] = away_team_abbreviation
        params["limit"] = limit
        params["offset"] = offset
        if extra_params:
            params.update(extra_params)

        primary = (
            league_name,
            date,
            season,
            match_id,
            home_team_id,
            away_team_id,
            home_team_abbreviation,
            away_team_abbreviation,
            (extra_params or {}).get("homeTeamName"),
            (extra_params or {}).get("awayTeamName"),
            (extra_params or {}).get("homeTeamDisplayName"),
            (extra_params or {}).get("awayTeamDisplayName"),
        )
        if not any(v is not None for v in primary):
            raise ValueError(
                "At least one primary filter is required "
                "(league_name, date, season, match_id, or a team filter)"
            )

        response = await self.http.get(
            f"{self.base_url}/highlights", params=params, headers=self._headers
        )
        return HighlightlyHighlightsResponse.model_validate(response.json())
