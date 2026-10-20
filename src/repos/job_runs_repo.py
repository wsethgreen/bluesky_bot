"""Postgres access for the job_runs table (append-only run heartbeat)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.job_run import JobRun


class JobRunsRepo:
    """Job run records: write-audit for the scheduler, read for status.

    Owns no transactions: methods flush but never commit, so callers
    control commit/rollback boundaries. The one exception is
    :meth:`prune`, which is self-contained housekeeping and commits its
    own delete.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        job_id: str,
        *,
        started_at: dt.datetime,
        finished_at: dt.datetime,
        ok: bool,
        summary: str | None = None,
        error: str | None = None,
    ) -> JobRun:
        """Append one run record (never updates: history is append-only)."""
        run = JobRun(
            job_id=job_id,
            started_at=started_at,
            finished_at=finished_at,
            ok=ok,
            summary=summary,
            error=error,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def latest_per_job(self, job_ids: list[str] | None = None) -> list[JobRun]:
        """The most recent run for each job, newest first.

        With ``job_ids``, one row per requested id (jobs that never ran
        are simply absent — callers decide how to report that).
        """
        stmt = select(JobRun)
        if job_ids is not None:
            stmt = stmt.where(JobRun.job_id.in_(job_ids))
        # Postgres DISTINCT ON keeps the first row per job_id, which is
        # the latest run given this ordering.
        stmt = stmt.distinct(JobRun.job_id).order_by(
            JobRun.job_id, JobRun.started_at.desc()
        )
        result = await self.session.execute(stmt)
        return sorted(result.scalars(), key=lambda r: r.started_at, reverse=True)

    async def prune(self, *, older_than: dt.datetime) -> int:
        """Delete runs started before ``older_than``; return rows removed."""
        result = await self.session.execute(
            delete(JobRun).where(JobRun.started_at < older_than)
        )
        await self.session.commit()
        return result.rowcount or 0
