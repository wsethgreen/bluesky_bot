from broker.broker import broker
from config.settings import get_settings
from db.session import AsyncSessionLocal
from jobs.base import WorkerContext, run_job
from jobs.post_pending import post_pending
from services.nba_service import NbaService
from services.nhl_service import NhlService


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
        await run_job("poster", post_pending, context)
    finally:
        await nhl.close()
        await nba.close()
