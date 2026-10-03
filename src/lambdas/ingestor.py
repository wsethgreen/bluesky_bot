"""Ingester Lambda: NHL API -> DynamoDB. No Bluesky calls here."""

from __future__ import annotations

import asyncio
import logging

from config.settings import get_settings
from repos.games_repo import GamesRepo
from repos.players_repo import PlayersRepo
from repos.posts import PostsRepo
from services.nhl_service import NhlService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def _ingest(event: dict) -> dict:
    settings = get_settings()
    team = (event.get("team") or settings.nhl_team_abbreviation).strip().upper()
    date = event.get("date")  # None = today
    game_id_override = event.get("game_id")

    games = GamesRepo()
    players = PlayersRepo()
    posts = PostsRepo()

    service = NhlService()
    try:
        day = date or service.today
        if game_id_override is not None:
            game_id = int(game_id_override)
            game = None
            source = "override"
        else:
            cached = games.get_game_by_date(day, team)
            if cached is not None:
                game_id = int(cached["game_id"])
                game = None
                source = "cache"
                logger.info("using cached game_id %s for %s", game_id, day)
            else:
                game = await service.get_team_game_on_date(date, team_abbreviation=team)
                if game is None or game.id is None:
                    logger.info("No game found.")
                    return {"status": "no_game", "team": team, "inserted": 0}
                games.upsert_game(
                    game_id=game.id,
                    date=day,
                    team=team,
                    game_state=game.game_state,
                    raw=game.model_dump(mode="json", by_alias=True),
                )
                game_id = game.id
                source = "api"

        goals = await service.get_goals_for_game(game_id)

        inserted = 0
        skipped = 0
        players_cached = 0
        for goal in goals:
            clip_id = goal.details.highlight_clip if goal.details else None
            if clip_id is None:
                skipped += 1
                continue
            scorer_id = goal.details.scoring_player_id if goal.details else None
            if scorer_id is not None and players.get_player(scorer_id) is None:
                try:
                    players.cache_player(scorer_id, await service.get_player(scorer_id))
                    players_cached += 1
                except Exception:
                    logger.warning("player fetch failed: %s", scorer_id, exc_info=True)
            if posts.claim_pending(
                clip_id=clip_id,
                game_id=game_id,
                team=team,
                event_id=goal.event_id,
            ):
                inserted += 1
            else:
                skipped += 1
        return {
            "status": "ok",
            "game_id": game_id,
            "inserted": inserted,
            "skipped": skipped,
            "source": source,
            "players_cached": players_cached,
        }
    finally:
        await service.client.http.aclose()


def handler(event: dict | None, context) -> dict:
    """Entry point. Event: {"game_id"?, "date"?, "team"?}."""
    result = asyncio.run(_ingest(event or {}))
    logger.info("ingest result: %s", result)
    return result
