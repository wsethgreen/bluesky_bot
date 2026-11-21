from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from taskiq import AsyncTaskiqDecoratedTask

from broker.broker import broker
from broker.scheduler import redis_schedule_source, schedule_task_by_time
from db.models.game import Game
from db.session import AsyncSessionLocal
from log.logger import get_logger
from repos.games_repo import GamesRepo
from repos.teams_repo import TeamsRepo
from services.nba_service import NbaService
from services.nhl_service import NhlService
from tasks.start_task import start_nba_game_tracking, start_nhl_game_tracking

logger = get_logger(__name__)

FINISHED = {"final", "off", "postponed", "canceled"}

SYNC_SCHEDULE = [{"cron": "0 9,13,17 * * *", "cron_offset": "America/New_York"}]


def _today() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _needs_tracking(game: Game) -> bool:
    """True when the game hasn't started tracking and isn't already over."""
    return (game.game_state or "").lower() not in FINISHED and not game.poll_schedule_id


async def _schedule_tracking(
    games_repo: GamesRepo,
    game: Game,
    tracking_task: AsyncTaskiqDecoratedTask,
) -> None:
    """(Re)arm the timer that starts live polling at game time."""
    if game.start_schedule_id:  # replace a timer from an earlier sync
        await redis_schedule_source.delete_schedule(game.start_schedule_id)

    run_at = max(game.start_time, datetime.now(UTC) + timedelta(seconds=5))
    schedule_id = await schedule_task_by_time(tracking_task, run_at, game.game_id)
    await games_repo.set_start_schedule_id(game.game_id, schedule_id)


@broker.task(task_name="sync_today_nhl_games", schedule=SYNC_SCHEDULE)
async def sync_today_nhl_games() -> None:
    today = _today()
    nhl_service = NhlService()
    try:
        async with AsyncSessionLocal() as session:
            teams_repo = TeamsRepo(session)
            games_repo = GamesRepo(session)

            scheduled = await nhl_service.get_team_game_on_date(today)
            if scheduled is None:
                return
            logger.info(
                "Found NHL game",
                date=f"{today.month}/{today.day}/{today.year}",
                game_id=scheduled.id,
                home_team=scheduled.home_team.abbrev,
                away_team=scheduled.away_team.abbrev,
            )

            for team in (scheduled.home_team, scheduled.away_team):
                if team is None or team.id is None:
                    continue
                stats = await nhl_service.get_team(team.id)
                if stats is not None:
                    await teams_repo.upsert_from_stats(stats)
            await session.flush()  # teams before the games FK insert

            game = await games_repo.upsert_game(nhl_service.build_game(scheduled))
            await session.commit()
            if not _needs_tracking(game):
                return

            await _schedule_tracking(games_repo, game, start_nhl_game_tracking)
            await session.commit()
    except Exception as e:
        logger.exception("sync_today_nhl_games failed", error=str(e))
    finally:
        await nhl_service.close()


@broker.task(task_name="sync_today_nba_games", schedule=SYNC_SCHEDULE)
async def sync_today_nba_games() -> None:
    today = _today()
    nba_service = NbaService()
    try:
        async with AsyncSessionLocal() as session:
            teams_repo = TeamsRepo(session)
            games_repo = GamesRepo(session)

            scheduled = await nba_service.get_team_game_on_date(today)
            if scheduled is None:
                return
            logger.info(
                "Found NBA game",
                date=f"{today.month}/{today.day}/{today.year}",
                game_id=scheduled.id,
                home_team=scheduled.home_competitor.team,
                away_team=scheduled.away_competitor.team,
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

            game = await games_repo.upsert_game(nba_service.build_game(scheduled))
            await session.commit()
            if not _needs_tracking(game):
                return

            await _schedule_tracking(games_repo, game, start_nba_game_tracking)
            await session.commit()
    except Exception as e:
        logger.exception("sync_today_nba_games failed", error=str(e))
    finally:
        await nba_service.close()
