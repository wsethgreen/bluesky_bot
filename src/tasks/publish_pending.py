from broker.broker import broker
from config.settings import get_settings
from db.session import AsyncSessionLocal
from jobs.base import WorkerContext
from jobs.post_pending import post_pending
from log.logger import get_logger
from services.nba_service import NbaService
from services.nhl_service import NhlService

logger = get_logger(__name__)


@broker.task(
    task_name="publish_pending",
    schedule=[{"cron": "* * * * *", "cron_offset": "America/New_York"}],
)
async def publish_pending():
    settings = get_settings()
    nhl = NhlService()
    nba = NbaService()
    context = WorkerContext(
        settings=settings, nhl=nhl, nba=nba, sessions=AsyncSessionLocal
    )
    try:
        await post_pending(context)
    except Exception as e:
        logger.error("Exception while publishing pending posts", error=str(e))
        raise
    finally:
        await nhl.close()
        await nba.close()
