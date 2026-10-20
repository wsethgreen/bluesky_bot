from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class JobRun(Base):
    """One recorded execution of a scheduled job (the worker heartbeat).

    ``run_job`` writes a row per run so scheduler health is durable and
    observable from outside the scheduler process — the old in-memory
    status dict died with the process and was never read by anything.

    Successes are recorded too: "the poster checked and found nothing"
    is the normal steady state, and the absence of recent rows is the
    actual alarm signal.

    Rows are pruned opportunistically against
    ``Settings.job_run_retention_days``.
    """

    __tablename__ = "job_runs"
    __table_args__ = (
        # Serves "latest run per job" (DISTINCT ON job_id ... ORDER BY
        # started_at DESC).
        Index("ix_job_runs_job_id_started_at", "job_id", text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
