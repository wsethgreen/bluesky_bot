from datetime import UTC, datetime, timedelta

from taskiq import AsyncTaskiqDecoratedTask, TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListRedisScheduleSource

from broker.broker import broker
from config.settings import get_redis_settings
from log.logger import get_logger

logger = get_logger(__name__)
redis_settings = get_redis_settings()
label_schedule_source = LabelScheduleSource(broker)
redis_schedule_source = ListRedisScheduleSource(
    redis_settings.redis_url.get_secret_value()
)


class StartupSyncScheduler(TaskiqScheduler):
    """Scheduler that kicks off the day's game sync once at startup.

    ``sync_today_games`` otherwise only fires at 09:00/13:00/17:00, so a
    deploy or restart after the last cron would never pick up that
    night's game. Jobs are idempotent, so a redundant sync is harmless.
    """

    async def startup(self) -> None:
        await super().startup()
        # Imported here because tasks.sync_today_games imports this module.
        from tasks.sync_today_games import sync_today_games

        try:
            await sync_today_games.kiq()
            logger.info("Enqueued startup sync_today_games")
        except Exception as err:
            logger.exception(
                "Failed to enqueue startup sync_today_games", error=str(err)
            )


scheduler = StartupSyncScheduler(
    broker=broker, sources=[label_schedule_source, redis_schedule_source]
)


async def schedule_task_by_interval(
    task: AsyncTaskiqDecoratedTask,
    interval: timedelta = timedelta(seconds=60),
    *args,
    **kwargs,
) -> str:
    try:
        schedule = await task.schedule_by_interval(
            redis_schedule_source, interval, *args, **kwargs
        )
        return schedule.schedule_id
    except Exception as err:
        logger.error(
            "Failed to schedule task by interval",
            args=args,
            kwargs=kwargs,
            error=str(err),
        )
        raise


async def schedule_task_by_time(
    task: AsyncTaskiqDecoratedTask,
    time: datetime,
    *args,
    **kwargs,
) -> str:
    try:
        schedule = await task.schedule_by_time(
            redis_schedule_source, time.astimezone(UTC), *args, **kwargs
        )
        return schedule.schedule_id
    except Exception as err:
        logger.error(
            "Failed to schedule task by time", args=args, kwargs=kwargs, error=str(err)
        )
        raise
