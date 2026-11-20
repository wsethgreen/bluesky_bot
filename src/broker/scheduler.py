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
scheduler = TaskiqScheduler(
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
