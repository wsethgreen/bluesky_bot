"""Container entrypoint for the scheduler/worker services.

Placeholder until real jobs are wired: parses the mode flag and idles so the
containers stay up (and stop gracefully) instead of crash-looping.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal

logger = logging.getLogger(__name__)


async def _idle() -> None:
    """Wait until SIGTERM/SIGINT so the container stays up but stops cleanly."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # e.g. Windows: KeyboardInterrupt still breaks the wait below
    await stop.wait()


async def _run(mode: str) -> None:
    logger.info("worker '%s' starting (placeholder, no jobs wired yet)", mode)
    await _idle()
    logger.info("worker '%s' stopping", mode)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(description="Run a worker process.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scheduler", action="store_true", help="Own the timers")
    group.add_argument("--worker", action="store_true", help="Consume claimed jobs")
    args = parser.parse_args()
    asyncio.run(_run("scheduler" if args.scheduler else "worker"))


if __name__ == "__main__":
    main()
