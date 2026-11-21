from taskiq import TaskiqEvents, TaskiqState
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from config.settings import get_redis_settings, get_settings
from log.logger import configure_logging

settings = get_redis_settings()


result_backend = RedisAsyncResultBackend(
    redis_url=settings.redis_url.get_secret_value(),
    result_ex_time=3600,
)


broker = RedisStreamBroker(
    url=settings.redis_url.get_secret_value()
).with_result_backend(result_backend)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _configure_worker_logging(state: TaskiqState) -> None:
    """Send stdlib logging through structlog in worker processes.

    Taskiq spawns its worker children, so the CLI's ``basicConfig`` in the
    parent never reaches them: without this the root logger stays at
    WARNING with no handler and every ``logger.info`` in ``jobs/`` is
    silently dropped.
    """
    configure_logging(level=get_settings().log_level)
