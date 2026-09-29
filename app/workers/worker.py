"""Worker pool (§6, §7). Concurrency limit, timeout, retry with backoff, cancellation.
RATE_LIMIT -> job WAITS for the server-defined delay (attempt not consumed), then retries when permitted."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import Settings, get_settings
from app.db.models import JobRecord
from app.domain.enums import ErrorCategory, ExecutionStatus
from app.logging_config import get_logger
from app.services.errors import RETRYABLE, RateLimited, classify_exception, record_error
from app.utils import dumps, loads
from app.workers.backoff import compute_backoff
from app.workers.queue import JobQueue

log = get_logger("workers")


@dataclass
class JobContext:
    job_id: str
    job_type: str
    attempt: int
    worker: str
    requested_by: str | None


Handler = Callable[[dict, JobContext], Awaitable[dict | None]]


class WorkerPool:
    def __init__(self, queue: JobQueue, handlers: dict[str, Handler], *, concurrency: int | None = None,
                 settings: Settings | None = None, name: str = "worker", poll_interval: float = 1.0):
        self.queue = queue
        self.handlers = handlers
        self.settings = settings or get_settings()
        self.concurrency = concurrency or self.settings.capacity.worker_count
        self.name = name
        self.poll_interval = poll_interval
        self._tasks: list[asyncio.Task] = []
        self._running: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._stop.clear()
        await self.queue.resume_stale_running()
        self._tasks = [asyncio.create_task(self._loop(f"{self.name}-{i}")) for i in range(self.concurrency)]
        log.info("worker_pool_started", workers=self.concurrency)

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        log.info("worker_pool_stopped")

    async def _loop(self, worker: str) -> None:
        while not self._stop.is_set():
            try:
                n = await self.run_once(worker)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("worker_loop_error", error=str(exc))
                n = 0
            if n == 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
                except asyncio.TimeoutError:
                    pass

    async def run_once(self, worker: str | None = None, limit: int = 1) -> int:
        """Claim and process up to `limit` jobs. Returns the number processed. Used by the loop, CLI and tests."""
        jobs = await self.queue.claim(worker or self.name, limit=limit)
        for job in jobs:
            await self._process(job, worker or self.name)
        return len(jobs)

    async def cancel_running(self, job_id: str) -> bool:
        task = self._running.get(job_id)
        if task:
            task.cancel()
            return True
        return await self.queue.cancel(job_id)

    # ------------------------------------------------------------------ processing
    async def _process(self, job: JobRecord, worker: str) -> None:
        handler = self.handlers.get(job.job_type)
        attempt = job.attempts + 1
        ctx = JobContext(job_id=job.id, job_type=job.job_type, attempt=attempt, worker=worker,
                         requested_by=job.requested_by)
        payload = loads(job.payload_json) or {}
        now = datetime.now(timezone.utc)
        status = ExecutionStatus.FAILED
        result: dict | None = None
        err_cat: ErrorCategory | None = None
        err_msg: str | None = None
        not_before: datetime | None = None
        consumed_attempt = True

        if handler is None:
            err_cat, err_msg = ErrorCategory.VALIDATION_ERROR, f"no handler registered for job type '{job.job_type}'"
        else:
            task = asyncio.ensure_future(handler(payload, ctx))
            self._running[job.id] = task
            try:
                result = await asyncio.wait_for(task, timeout=self.settings.capacity.task_timeout_seconds)
                status = ExecutionStatus.COMPLETED
            except RateLimited as exc:
                status = ExecutionStatus.WAITING
                consumed_attempt = False
                err_cat, err_msg = ErrorCategory.RATE_LIMIT, exc.message
                not_before = now + timedelta(seconds=exc.wait_seconds)  # server-defined, used verbatim
            except asyncio.TimeoutError:
                err_cat, err_msg = ErrorCategory.NETWORK_ERROR, f"timed out after {self.settings.capacity.task_timeout_seconds}s"
            except asyncio.CancelledError:
                status, err_cat, err_msg = ExecutionStatus.CANCELLED, None, "cancelled"
            except Exception as exc:  # noqa: BLE001
                err_cat, err_msg = classify_exception(exc), f"{type(exc).__name__}: {exc}"
            finally:
                self._running.pop(job.id, None)

        if status == ExecutionStatus.FAILED and err_cat in RETRYABLE and attempt < job.max_attempts:
            status = ExecutionStatus.RETRYING
            delay = compute_backoff(attempt, self.settings.capacity.backoff_base_seconds,
                                    self.settings.capacity.backoff_max_seconds)
            not_before = now + timedelta(seconds=delay)

        async with self.queue.session_factory() as db:
            fresh = await db.get(JobRecord, job.id)
            if fresh is None:
                return
            if fresh.status == ExecutionStatus.CANCELLED.value:
                await db.commit()
                return
            fresh.attempts = attempt if consumed_attempt else fresh.attempts
            fresh.status = status.value
            fresh.not_before = not_before
            fresh.error_category = err_cat.value if err_cat else None
            fresh.error_message = err_msg
            fresh.result_json = dumps(result) if result is not None else None
            fresh.finished_at = now if status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
                                                  ExecutionStatus.CANCELLED) else None
            if status in (ExecutionStatus.WAITING, ExecutionStatus.RETRYING):
                fresh.started_at = None
                fresh.worker = None
            if err_cat and status != ExecutionStatus.WAITING:
                await record_error(db, err_cat, err_msg or "job failed", component="workers",
                                   session_id=payload.get("session_id"), case_id=payload.get("case_id"),
                                   operator=job.requested_by, details={"job_id": job.id, "job_type": job.job_type,
                                                                        "attempt": attempt, "status": status.value})
            await db.commit()
        log.info("job_processed", job_id=job.id, job_type=job.job_type, status=status.value, attempt=attempt,
                 error=err_msg)
