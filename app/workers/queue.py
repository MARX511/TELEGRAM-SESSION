"""Persistent task queue (§6, §31). Jobs live in the `jobs` table so they survive crashes and can be resumed.
Redis, when configured, is only used as a wake-up signal; the DB is the source of truth."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import JobRecord
from app.domain.enums import ExecutionStatus
from app.services.errors import ValidationFailed
from app.utils import dumps

CLAIMABLE = (ExecutionStatus.PENDING.value, ExecutionStatus.RETRYING.value, ExecutionStatus.WAITING.value)
CANCELLABLE = CLAIMABLE


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobQueue:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], settings: Settings | None = None):
        self.session_factory = session_factory
        self.settings = settings or get_settings()

    async def enqueue(self, job_type: str, payload: dict | None = None, *, requested_by: str | None = None,
                      max_attempts: int | None = None, not_before: datetime | None = None,
                      db: AsyncSession | None = None) -> JobRecord:
        async def _do(session: AsyncSession) -> JobRecord:
            pending = (await session.execute(select(func.count(JobRecord.id)).where(
                JobRecord.status.in_(CLAIMABLE)))).scalar_one()
            if pending >= self.settings.capacity.queue_max:
                raise ValidationFailed(f"queue is full ({pending} >= CAPACITY_QUEUE_MAX)")
            job = JobRecord(job_type=job_type, payload_json=dumps(payload or {}), status=ExecutionStatus.PENDING.value,
                            max_attempts=max_attempts if max_attempts is not None else self.settings.capacity.max_retries + 1,
                            not_before=not_before, requested_by=requested_by)
            session.add(job)
            await session.flush()
            return job

        if db is not None:
            return await _do(db)
        async with self.session_factory() as session:
            job = await _do(session)
            await session.commit()
            return job

    async def enqueue_many(self, job_type: str, payloads: list[dict], **kw) -> list[JobRecord]:
        async with self.session_factory() as session:
            jobs = [await self.enqueue(job_type, p, db=session, **kw) for p in payloads]
            await session.commit()
            return jobs

    async def get(self, job_id: str) -> JobRecord | None:
        async with self.session_factory() as session:
            return await session.get(JobRecord, job_id)

    async def cancel(self, job_id: str) -> bool:
        async with self.session_factory() as session:
            res = await session.execute(update(JobRecord).where(JobRecord.id == job_id, JobRecord.status.in_(CANCELLABLE))
                                        .values(status=ExecutionStatus.CANCELLED.value, finished_at=_now()))
            await session.commit()
            return res.rowcount > 0

    async def claim(self, worker: str, limit: int = 1) -> list[JobRecord]:
        now = _now()
        async with self.session_factory() as session:
            q = (select(JobRecord).where(JobRecord.status.in_(CLAIMABLE),
                                          (JobRecord.not_before.is_(None)) | (JobRecord.not_before <= now))
                 .order_by(JobRecord.created_at).limit(limit))
            if session.bind.dialect.name == "postgresql":
                q = q.with_for_update(skip_locked=True)
            jobs = list((await session.execute(q)).scalars().all())
            for j in jobs:
                j.status = ExecutionStatus.RUNNING.value
                j.started_at = now
                j.worker = worker
            await session.commit()
            return jobs

    async def stats(self) -> dict[str, int]:
        async with self.session_factory() as session:
            rows = (await session.execute(select(JobRecord.status, func.count()).group_by(JobRecord.status))).all()
        out = {s.value: 0 for s in ExecutionStatus}
        out.update({r[0]: r[1] for r in rows})
        return out

    async def resume_stale_running(self, older_than_seconds: int = 0) -> int:
        """Crash recovery (§31): jobs left RUNNING by a dead worker go back to PENDING."""
        cutoff = _now() - timedelta(seconds=older_than_seconds)
        async with self.session_factory() as session:
            res = await session.execute(update(JobRecord).where(JobRecord.status == ExecutionStatus.RUNNING.value,
                                                                JobRecord.started_at <= cutoff)
                                        .values(status=ExecutionStatus.PENDING.value, worker=None, started_at=None))
            await session.commit()
            return res.rowcount

    async def list_jobs(self, status: str | None = None, limit: int = 100) -> list[JobRecord]:
        async with self.session_factory() as session:
            q = select(JobRecord).order_by(JobRecord.created_at.desc()).limit(limit)
            if status:
                q = q.where(JobRecord.status == status)
            return list((await session.execute(q)).scalars().all())
