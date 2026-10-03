"""Scheduled jobs. Each job takes a WorkerContext and returns a summary string."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from models.highlighty import HighlightlyMatch

if TYPE_CHECKING:
    from workers.scheduler import WorkerContext

logger = logging.getLogger(__name__)


def _involves(match: HighlightlyMatch, team: str) -> bool:
    sides = (match.home_team, match.away_team)
    return any(s is not None and (s.abbreviation or "").upper() == team for s in sides)


def _describe(match: HighlightlyMatch) -> str:
    home = match.home_team.abbreviation if match.home_team else "?"
    away = match.away_team.abbreviation if match.away_team else "?"
    state = match.state.description if match.state else "unknown"
    return f"{away} @ {home} ({state}, start {match.date})"


async def poll_nhl_live(ctx: WorkerContext) -> str:
    """Every 5 min: find the followed team's game today and report its state.

    Detection only for now — the fetch-highlights/post steps plug in here next.
    """
    if ctx.highlighty is None:
        return "skipped: Highlightly client not configured"
    team = ctx.settings.followed_team_abbreviation.strip().upper()
    if not team:
        return "skipped: FOLLOWED_TEAM_ABBREVIATION not set"

    resp = await ctx.highlighty.get_todays_matches(
        league="NHL", timezone=ctx.settings.app_timezone
    )
    game = next((m for m in resp.data if _involves(m, team)), None)
    if game is None:
        return f"no {team} game today ({len(resp.data)} NHL games)"

    summary = _describe(game)
    logger.info("game found: %s", summary)
    # Next: fetch highlights for game.id, diff vs posted table, post new ones.
    return summary
