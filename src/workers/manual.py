"""Manual one-shot runner for the poll worker and poster.

Runs a single pass of a job without the scheduler, without the game-day
gate, and (with ``--force``) without the final-state grace window /
4-hour-window short-circuits. Useful long after a game ends to pick up
late-arriving highlight clips.

Run inside the compose network (scheduler container has DB access):

    docker compose exec scheduler python -m workers.manual status
    docker compose exec scheduler python -m workers.manual poll --force
    docker compose exec scheduler python -m workers.manual poll --date 2026-10-24 --force
    docker compose exec scheduler python -m workers.manual post --force
    docker compose exec scheduler python -m workers.manual post --dry-run

``status`` prints the latest recorded run per job (from ``job_runs``)
followed by the pending posts — the quickest way to answer "are the
workers alive and what did they last do?".

``poll --date YYYY-MM-DD --force`` re-polls a *previous* day's stored
game (``poll --force`` only reaches today's game), which is how a
missed highlight clip is backfilled once its date has passed; the
matching ``post --force`` then publishes it outside the 4-hour window.

``--dry-run`` for ``post`` claims nothing and posts nothing; it only
prints the oldest pending posts (goal text + highlight video rows).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from config.settings import get_settings
from db.engine import engine
from db.session import AsyncSessionLocal
from jobs.base import WorkerContext
from jobs.poll_nhl_live import PollWorker
from jobs.post_pending import post_pending
from repos.job_runs_repo import JobRunsRepo
from repos.posts_repo import PostsRepo
from services.nhl_service import NhlService
from utils.date import normalize_date

logger = logging.getLogger(__name__)


async def _poll(force: bool, day: str | None) -> None:
    settings = get_settings()
    nhl = NhlService()
    worker = PollWorker(
        nhl=nhl,
        sessions=AsyncSessionLocal,
        team_abbreviation=settings.nhl_team_abbreviation,
        final_grace=timedelta(minutes=settings.final_grace_minutes),
    )
    try:
        summary = await worker.poll_once(force=force, day=day)
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
                        f"pending: id={p.id} kind={p.kind} clip_id={p.clip_id} "
                        f"game={p.game_id} team={p.team} attempts={p.attempts} "
                        f"defunct={p.defunct} text={p.text!r}"
                    )
            return
        summary = await post_pending(ctx, force=force)
        print(f"post: {summary}")
    finally:
        await nhl.client.http.aclose()
        await nhl.stats.http.aclose()
        await engine.dispose()


async def _status() -> None:
    async with AsyncSessionLocal() as session:
        runs = JobRunsRepo(session)
        latest = await runs.latest_per_job()
        if not latest:
            print("no job runs recorded yet (scheduler never ran a job)")
        else:
            now = datetime.now(UTC)
            for r in latest:
                age = now - r.started_at
                state = "OK  " if r.ok else "FAIL"
                print(
                    f"job {r.job_id}: {state} {age.total_seconds():.0f}s ago "
                    f"({r.started_at.isoformat()}) — {r.summary or r.error or ''}"
                )
        posts = PostsRepo(session)
        pending = await posts.list_pending(limit=10)
        if not pending:
            print("no pending posts")
        else:
            for p in pending:
                print(
                    f"pending: id={p.id} kind={p.kind} clip_id={p.clip_id} "
                    f"game={p.game_id} team={p.team} attempts={p.attempts} "
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
        help="poll: run one poll cycle; post: post oldest pending post; "
        "status: latest job run per job + pending posts",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Poll final games / post outside the 4-hour window",
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="(poll only) re-poll that date's stored game instead of "
        "today's — pair with --force to backfill a finished game",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="(post only) print the oldest pending posts without posting",
    )
    args = parser.parse_args()
    if args.date is not None:
        if args.action != "poll":
            parser.error("--date only applies to the poll action")
        try:
            normalize_date(args.date)
        except ValueError as exc:
            parser.error(str(exc))

    if args.action == "poll":
        asyncio.run(_poll(force=args.force, day=args.date))
    elif args.action == "post":
        asyncio.run(_post(force=args.force, dry_run=args.dry_run))
    else:
        asyncio.run(_status())


if __name__ == "__main__":
    main()
