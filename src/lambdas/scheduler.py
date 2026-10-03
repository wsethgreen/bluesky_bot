"""Scheduler Lambda: check today's game, upsert it, set ingestor/poster cadence.

Runs daily (see DailySync in stacks/bot_stack.py). On game days it creates/updates
two EventBridge Scheduler schedules firing every 5 minutes, staggered so the
ingester lands ~1 min before the poster:

- ingester: minutes 4,9,14,... (e.g. 12:04, 12:09)
- poster:   minutes 0,5,10,... (e.g. 12:05, 12:10)

On off days the burst schedules are deleted, leaving the hourly baseline.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from config.settings import get_settings
from repos.games_repo import GamesRepo
from services.nhl_service import NhlService
from services.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

INGESTOR_CRON = "cron(4,9,14,19,24,29,34,39,44,49,54,59 * * * ? *)"
POSTER_CRON = "cron(0,5,10,15,20,25,30,35,40,45,50,55 * * * ? *)"

GAME_WINDOW_LEAD = timedelta(minutes=30)
GAME_WINDOW_LENGTH = timedelta(hours=5)


def _parse_puck_drop(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("unparseable startTimeUTC: %r", raw)
        return None


async def _run(event: dict) -> dict:
    settings = get_settings()
    team = (event.get("team") or settings.nhl_team_abbreviation).strip().upper()

    service = NhlService()
    try:
        day = event.get("date") or service.today
        games = GamesRepo()
        schedules = SchedulerService()
        ingestor_schedule = os.environ.get(
            "INGESTOR_SCHEDULE_NAME", "bluesky-ingestor-gameday"
        )
        poster_schedule = os.environ.get(
            "POSTER_SCHEDULE_NAME", "bluesky-poster-gameday"
        )
        cached = games.get_game_by_date(day, team)

        if cached is not None:
            game_id = int(cached["game_id"])
            game_state = cached.get("game_state")
            raw = cached.get("raw") or {}
            source = "cache"
            logger.info("using cached game %s for %s", game_id, day)
        else:
            live = await service.get_team_game_on_date(
                event.get("date"), team_abbreviation=team
            )
            if live is None or live.id is None:
                schedules.delete_schedule(ingestor_schedule)
                schedules.delete_schedule(poster_schedule)
                return {"status": "no_game", "team": team}
            game_id = live.id
            game_state = live.game_state
            raw = live.model_dump(mode="json", by_alias=True)
            games.upsert_game(
                game_id=game_id,
                date=day,
                team=team,
                game_state=game_state,
                raw=raw,
            )
            source = "api"

        puck_drop = _parse_puck_drop(raw.get("startTimeUTC"))
        if puck_drop is None:
            start = datetime.now(UTC)
            end = start + timedelta(hours=6)
        else:
            start = puck_drop - GAME_WINDOW_LEAD
            end = puck_drop + GAME_WINDOW_LENGTH

        payload = {"game_id": game_id, "date": day, "team": team}
        schedules.upsert_schedule(
            name=ingestor_schedule,
            cron=INGESTOR_CRON,
            start=start,
            end=end,
            target_arn=os.environ["INGESTOR_FUNCTION_ARN"],
            role_arn=os.environ["SCHEDULER_EXEC_ROLE_ARN"],
            payload=payload,
        )
        schedules.upsert_schedule(
            name=poster_schedule,
            cron=POSTER_CRON,
            start=start,
            end=end,
            target_arn=os.environ["POSTER_FUNCTION_ARN"],
            role_arn=os.environ["SCHEDULER_EXEC_ROLE_ARN"],
            payload=payload,
        )
        return {
            "status": "game_day",
            "team": team,
            "game_id": game_id,
            "source": source,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }
    finally:
        await service.client.http.aclose()


def handler(event: dict | None, context) -> dict:
    """Entry point. Event: {"date"?, "team"?} (defaults from env)."""
    result = asyncio.run(_run(event or {}))
    logger.info("scheduler result: %s", result)
    return result
