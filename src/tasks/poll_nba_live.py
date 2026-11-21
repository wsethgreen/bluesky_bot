from datetime import UTC, datetime, timedelta

from broker.broker import broker
from broker.scheduler import redis_schedule_source
from config.settings import get_settings
from db.session import AsyncSessionLocal
from jobs.base import WorkerContext
from jobs.poll_nba_live import poll_nba_live as poll_nba_live_job
from log.logger import get_logger
from repos.games_repo import GamesRepo
from services.nba_service import NbaService
from services.nhl_service import NhlService

logger = get_logger(__name__)


@broker.task(task_name="poll_nba_live")
async def poll_nba_live(game_id: int) -> None:
    settings = get_settings()
    nhl = NhlService()
    nba = NbaService()
    context = WorkerContext(
        settings=settings, nhl=nhl, nba=nba, sessions=AsyncSessionLocal
    )
    try:
        async with AsyncSessionLocal() as session:
            games_repo = GamesRepo(session)
            game = await games_repo.get_game(game_id)
            if game is None or not game.poll_schedule_id:
                logger.warning(f"Game with game_id {game_id} not found")
                return

            state = (game.game_state or "").lower()
            grace = timedelta(minutes=settings.nba.video_grace_minutes)
            ended = game.ended_at
            grace_over = ended is not None and datetime.now(UTC) - ended > grace

            if state in {"postponed", "canceled"} or grace_over:
                logger.info("Game is over; stopping poll schedule")
                await redis_schedule_source.delete_schedule(game.poll_schedule_id)
                await games_repo.set_poll_schedule_id(game_id, None)
                await session.commit()
                return

        logger.info(
            "Polling nba live game", game_id=game_id, schedule_id=game.poll_schedule_id
        )

        await poll_nba_live_job(ctx=context)
    except Exception as e:
        logger.error("Exception while polling nba live game", error=str(e))
        raise
    finally:
        await nhl.close()
        await nba.close()
