"""Container entrypoint for the singleton scheduler service.

Owns all APScheduler timers (game-day check + poll + poster stub).
Run with ``python -m workers.run --scheduler``. ``--worker`` is kept as a
deprecated alias so the old compose service name doesn't crash-loop.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal

from config.settings import get_settings
from db.engine import engine
from db.session import AsyncSessionLocal
from services.nhl_service import NhlService
from workers.jobs import check_game_day
from workers.scheduler import CHECK_JOB_ID, WorkerContext, build_scheduler, run_job

logger = logging.getLogger(__name__)


async def _run_scheduler() -> None:
    settings = get_settings()
    nhl = NhlService()
    ctx = WorkerContext(settings=settings, nhl=nhl, sessions=AsyncSessionLocal)
    scheduler = build_scheduler(ctx)
    scheduler.start()
    logger.info(
        "scheduler started (check %02d:%02d %s, poll every %dm)",
        settings.game_day_check_hour,
        settings.game_day_check_minute,
        settings.app_timezone,
        settings.poll_interval_minutes,
    )
    # Decide initial paused/resumed state now so restarts don't wait for
    # the next cron tick (or poll all day when there's no game).
    await run_job(CHECK_JOB_ID, check_game_day, ctx)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # e.g. Windows: KeyboardInterrupt still breaks the wait below
    try:
        await stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("scheduler stopping")
        scheduler.shutdown(wait=False)
        await nhl.client.http.aclose()
        await nhl.stats.http.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(description="Run the scheduler singleton.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scheduler", action="store_true", help="Own the timers")
    group.add_argument(
        "--worker", action="store_true", help="Deprecated alias for --scheduler"
    )
    args = parser.parse_args()
    if args.worker:
        logger.warning("--worker is deprecated; running the scheduler singleton")
    asyncio.run(_run_scheduler())


if __name__ == "__main__":
    main()
