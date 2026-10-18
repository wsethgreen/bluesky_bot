"""Manual one-shot runner for the poll worker and poster.

Runs a single pass of a job without the scheduler, without the game-day
gate, and (with ``--force``) without the final-state / 4-hour-window
short-circuits. Useful after a game ends to pick up late-arriving
highlight clips.

Run inside the compose network (scheduler container has DB access):

    docker compose exec scheduler python -m workers.manual status
    docker compose exec scheduler python -m workers.manual poll --force
    docker compose exec scheduler python -m workers.manual post --force
    docker compose exec scheduler python -m workers.manual post --dry-run

``--dry-run`` for ``post`` claims nothing and posts nothing; it only
prints what the oldest pending clip is.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from config.settings import get_settings
from db.engine import engine
from db.session import AsyncSessionLocal
from repos.posts_repo import PostsRepo
from services.nhl_service import NhlService
from workers.jobs import post_pending_clips
from workers.poll_worker import PollWorker
from workers.scheduler import WorkerContext

logger = logging.getLogger(__name__)


async def _poll(force: bool) -> None:
    settings = get_settings()
    nhl = NhlService()
    worker = PollWorker(
        nhl=nhl,
        sessions=AsyncSessionLocal,
        team_abbreviation=settings.nhl_team_abbreviation,
    )
    try:
        summary = await worker.poll_once(force=force)
        print(f"poll: {summary}")
    finally:
        await nhl.client.http.aclose()
        await nhl.stats.http.aclose()
        await engine.dispose()


async def _post(force: bool, dry_run: bool) -> None:
    settings = get_settings()
    nhl = NhlService()
    ctx = WorkerContext(settings=settings, nhl=nhl, sessions=AsyncSessionLocal)
    try:
        if dry_run:
            async with AsyncSessionLocal() as session:
                posts = PostsRepo(session)
                pending = await posts.list_pending(limit=5)
                if not pending:
                    print("no pending posts")
                    return
                for p in pending:
                    print(
                        f"pending: post_id={p.post_id} game={p.game_id} "
                        f"team={p.team} attempts={p.attempts} "
                        f"defunct={p.defunct} text={p.text!r}"
                    )
            return
        summary = await post_pending_clips(ctx, force=force)
        print(f"post: {summary}")
    finally:
        await nhl.client.http.aclose()
        await nhl.stats.http.aclose()
        await engine.dispose()


async def _status() -> None:
    async with AsyncSessionLocal() as session:
        posts = PostsRepo(session)
        pending = await posts.list_pending(limit=10)
        if not pending:
            print("no pending posts")
            return
        for p in pending:
            print(
                f"pending: post_id={p.post_id} game={p.game_id} "
                f"team={p.team} attempts={p.attempts} "
                f"defunct={p.defunct} text={p.text!r}"
            )
    await engine.dispose()


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(
        description="Run one poll/post pass manually (bypasses scheduler + gates)."
    )
    parser.add_argument(
        "action",
        choices=["poll", "post", "status"],
        help="poll: run one poll cycle; post: post oldest pending clip; "
        "status: list pending clips without posting",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Poll final games / post outside the 4-hour window",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="(post only) print the oldest pending clip without posting",
    )
    args = parser.parse_args()

    if args.action == "poll":
        asyncio.run(_poll(force=args.force))
    elif args.action == "post":
        asyncio.run(_post(force=args.force, dry_run=args.dry_run))
    else:
        asyncio.run(_status())


if __name__ == "__main__":
    main()
