from datetime import timedelta

from broker.broker import broker
from broker.scheduler import schedule_task_by_interval
from db.session import AsyncSessionLocal
from log.logger import get_logger
from repos.games_repo import GamesRepo
from tasks.poll_nba_live import poll_nba_live
from tasks.poll_nhl_live import poll_nhl_live

logger = get_logger(__name__)


async def _start_tracking(game_id: int, poll_task) -> None:
    async with AsyncSessionLocal() as session:
        repo = GamesRepo(session)
        game = await repo.get_game_for_update(
            game_id
        )  # a second concurrent run waits here
        if game is None:
            raise ValueError(f"Game {game_id} not found")
        if game.poll_schedule_id or game.ended_at:
            return  # already tracking, or over

        poll_id = await schedule_task_by_interval(
            poll_task, timedelta(seconds=60), game_id
        )
        await repo.set_poll_schedule_id(game_id, poll_id)
        await repo.set_start_schedule_id(game_id, None)  # timer has fired
        await session.commit()  # releases the lock
        logger.info("Polling started", game_id=game_id, poll_id=poll_id)

    await poll_task.kiq(game_id)


@broker.task(task_name="start_nhl_game_tracking")
async def start_nhl_game_tracking(game_id: int) -> None:
    await _start_tracking(game_id, poll_nhl_live)


@broker.task(task_name="start_nba_game_tracking")
async def start_nba_game_tracking(game_id: int) -> None:
    await _start_tracking(game_id, poll_nba_live)
