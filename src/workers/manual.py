"""Manual one-shot runner for the poll worker and poster.

Runs a single pass of a job without the scheduler, without the game-day
gate, and (with ``--force``) without the final-state grace window /
posting-window short-circuits. Useful long after a game ends to pick up
late-arriving highlight clips.

Run inside the compose network (scheduler container has DB access):

    docker compose exec scheduler python -m workers.manual status
    docker compose exec scheduler python -m workers.manual poll --force
    docker compose exec scheduler python -m workers.manual poll --date 2026-10-24 --force
    docker compose exec scheduler python -m workers.manual poll-nba --force
    docker compose exec scheduler python -m workers.manual poll-nba --date 2026-11-09 --force
    docker compose exec scheduler python -m workers.manual post --force
    docker compose exec scheduler python -m workers.manual post --dry-run

``status`` prints the latest recorded run per job (from ``job_runs``)
followed by the pending posts and any defunct posts the poster gave up
on — the quickest way to answer "are the workers alive, what did they
last do, and is anything waiting on a human?"

``poll --date YYYY-MM-DD --force`` re-polls a *previous* day's stored
game (plain ``--force`` only reaches today's game), which is how a
missed highlight clip is backfilled once its date has passed; the
matching ``post --force`` then publishes it outside the posting window.
``poll-nba --date`` works the same way for the NBA poller (ESPN's
scoreboard serves past dates too): it's how a game that ended while the
scheduler was down gets its FINAL state and remaining quarter posts.

``--dry-run`` for ``post`` claims nothing and posts nothing; it only
prints the oldest pending posts (goal text + highlight video rows).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from config.settings import get_settings
from db.engine import engine
from db.session import AsyncSessionLocal
from jobs.base import WorkerContext
from jobs.poll_nba_live import NbaPollWorker
from jobs.poll_nhl_live import PollWorker
from jobs.post_pending import post_pending
from repos.job_runs_repo import JobRunsRepo
from repos.posts_repo import PostsRepo
from services.nba_service import NbaService
from services.nhl_service import NhlService
from utils.date import normalize_date

if TYPE_CHECKING:
    from db.models.post import Post

logger = logging.getLogger(__name__)


async def _poll(force: bool, day: str | None) -> None:
    settings = get_settings()
    nhl = NhlService()
    worker = PollWorker(
        nhl=nhl,
        sessions=AsyncSessionLocal,
        team_abbreviation=settings.nhl.team_abbreviation,
        final_grace=timedelta(minutes=settings.final_grace_minutes),
    )
    try:
        summary = await worker.poll_once(force=force, day=day)
        print(f"poll: {summary}")
    finally:
        await nhl.client.http.aclose()
        await nhl.stats.http.aclose()
        await engine.dispose()


async def _poll_nba(force: bool, day: str | None) -> None:
    # ``day`` backfills a previous day's stored game (ESPN's scoreboard
    # serves past dates), mirroring the NHL poller's ``--date``.
    settings = get_settings()
    nba = NbaService()
    worker = NbaPollWorker(
        nba=nba,
        sessions=AsyncSessionLocal,
        team_abbreviation=settings.nba.team_abbreviation,
        video_grace=timedelta(minutes=settings.nba.video_grace_minutes),
        video_max_duration_seconds=settings.nba.video_max_duration_seconds,
    )
    try:
        summary = await worker.poll_once(force=force, day=day)
        print(f"poll-nba: {summary}")
    finally:
        await nba.client.http.aclose()
        await nba.content.http.aclose()
        await engine.dispose()


async def _post(force: bool, dry_run: bool) -> None:
    settings = get_settings()
    nhl = NhlService()
    nba = NbaService()
    ctx = WorkerContext(settings=settings, nhl=nhl, nba=nba, sessions=AsyncSessionLocal)
    try:
        if dry_run:
            async with AsyncSessionLocal() as session:
                posts = PostsRepo(session)
                pending = await posts.list_pending(limit=5)
                if not pending:
                    print("no pending posts")
                    return
                for p in pending:
                    print(_post_line("pending", p))
            return
        summary = await post_pending(ctx, force=force)
        print(f"post: {summary}")
    finally:
        await nhl.client.http.aclose()
        await nhl.stats.http.aclose()
        await nba.client.http.aclose()
        await nba.content.http.aclose()
        await engine.dispose()


def _post_line(prefix: str, post: Post) -> str:
    """One ``status``/``--dry-run`` line for a post row.

    ``next_attempt_at`` is the retry-backoff deadline recorded when an
    attempt failed (None means claimable now), so a row sitting in
    ``pending`` can be told apart from one that is spacing out its
    retries.
    """
    return (
        f"{prefix}: id={post.id} kind={post.kind} clip_id={post.clip_id} "
        f"game={post.game_id} team={post.team} attempts={post.attempts} "
        f"defunct={post.defunct} next_attempt_at={post.next_attempt_at} "
        f"text={post.text!r}"
    )


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
                print(_post_line("pending", p))
        # The rows the poster gave up on: nothing retries them, so this
        # is the only section that needs a human (recover by clearing
        # defunct/attempts/next_attempt_at, then ``post --force``). A
        # clean "none" is printed too, so its absence is a signal.
        defunct = await posts.list_defunct(limit=10)
        if not defunct:
            print("no defunct posts")
        else:
            print(f"defunct posts ({len(defunct)}, newest first):")
            for p in defunct:
                print(_post_line("defunct", p) + f" last_error={p.last_error!r}")
    await engine.dispose()


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    # Post text and recorded errors carry emoji (team marks, goal
    # call-ups) that a Windows console's cp1252 cannot encode — replace
    # the offending characters instead of letting a print die halfway
    # through ``status``. Nothing changes where stdout is already UTF-8
    # (the compose containers).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        description="Run one poll/post pass manually (bypasses scheduler + gates)."
    )
    parser.add_argument(
        "action",
        choices=["poll", "poll-nba", "post", "status"],
        help="poll: one NHL poll cycle; poll-nba: one NBA poll cycle; "
        "post: post oldest pending post; "
        "status: latest job run per job + pending and defunct posts",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Poll settled games / post outside the posting window",
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="(poll / poll-nba only) re-poll that date's stored game "
        "instead of today's — pair with --force to backfill a finished game",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="(post only) print the oldest pending posts without posting",
    )
    args = parser.parse_args()
    if args.date is not None:
        if args.action not in ("poll", "poll-nba"):
            parser.error("--date only applies to the poll actions")
        try:
            normalize_date(args.date)
        except ValueError as exc:
            parser.error(str(exc))

    if args.action == "poll":
        asyncio.run(_poll(force=args.force, day=args.date))
    elif args.action == "poll-nba":
        asyncio.run(_poll_nba(force=args.force, day=args.date))
    elif args.action == "post":
        asyncio.run(_post(force=args.force, dry_run=args.dry_run))
    else:
        asyncio.run(_status())


if __name__ == "__main__":
    main()
