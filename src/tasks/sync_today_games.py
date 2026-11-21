from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from broker.broker import broker
from broker.scheduler import redis_schedule_source, schedule_task_by_time
from db.session import AsyncSessionLocal
from log.logger import get_logger
from repos.games_repo import GamesRepo
from repos.teams_repo import TeamsRepo
from services.nba_service import NbaService
from services.nhl_service import NhlService
from tasks.start_task import start_nba_game_tracking, start_nhl_game_tracking

logger = get_logger(__name__)

FINISHED = {"final", "off", "postponed", "canceled"}

SPORTS = [
    ("nhl", NhlService, start_nhl_game_tracking),
    ("nba", NbaService, start_nba_game_tracking),
]


@broker.task(
    task_name="sync_today_games",
    schedule=[{"cron": "0 9,13,17 * * *", "cron_offset": "America/New_York"}],
)
async def sync_today_games() -> None:
    today = datetime.now(ZoneInfo("America/New_York")).date()

    async with AsyncSessionLocal() as session:
        games_repo = GamesRepo(session)
        teams_repo = TeamsRepo(session)

        for league, service_cls, tracking_task in SPORTS:
            service = None
            try:
                service = service_cls()
                scheduled = await service.get_team_game_on_date(today)
                if not scheduled:
                    continue

                if league == "nba":
                    logger.info(
                        "Found game",
                        league=league,
                        date=today,
                        home_team=scheduled.home_competitor,
                        away_team=scheduled.away_competitor,
                    )
                    for comp in (scheduled.home_competitor, scheduled.away_competitor):
                        info = comp.team if comp else None
                        if info is None or info.id is None or not info.abbreviation:
                            continue
                        await teams_repo.upsert_from_nba(
                            team_id=info.id,
                            display_name=info.display_name or "",
                            tri_code=info.abbreviation,
                        )
                    await session.flush()  # teams before the games FK insert
                else:
                    logger.info(
                        "Found game",
                        league=league,
                        date=today,
                        home_team=scheduled.home_team,
                        away_team=scheduled.away_team,
                    )
                    for team in (scheduled.home_team, scheduled.away_team):
                        if team is None or team.id is None:
                            continue
                        stats = await service.get_team(team.id)
                        if stats is None:
                            continue
                        await teams_repo.upsert_from_stats(stats)
                    await session.flush()  # teams before the games FK insert

                game = await games_repo.upsert_game(service.build_game(scheduled))
                await session.commit()

                if (game.game_state or "").lower() in FINISHED or game.poll_schedule_id:
                    continue

                if game.start_schedule_id:  # replace a timer from an earlier sync
                    await redis_schedule_source.delete_schedule(game.start_schedule_id)

                run_at = max(
                    game.start_time,
                    datetime.now(UTC) + timedelta(seconds=5),
                )
                schedule_id = await schedule_task_by_time(
                    tracking_task, run_at, game.game_id
                )
                await games_repo.set_start_schedule_id(game.game_id, schedule_id)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.exception(f"sync_today_games failed for {league}", error=str(e))
            finally:
                if service is not None:
                    await service.close()
