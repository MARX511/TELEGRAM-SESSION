from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import JobRecord
from app.domain.enums import ErrorCategory, ExecutionStatus, ProxyStatus
from app.services.errors import RateLimited, ValidationFailed
from app.services.proxies import check_proxy, create_proxy, to_spec
from app.workers.queue import JobQueue
from app.workers.worker import WorkerPool


async def test_proxy_connectivity_check(db):
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        p = await create_proxy(db, host="127.0.0.1", port=port, protocol="socks5", name="local", actor="t")
        await check_proxy(db, p, actor="t")
        assert p.status == ProxyStatus.UP.value and p.latency_ms is not None and p.last_check
        dead = await create_proxy(db, host="127.0.0.1", port=1, protocol="socks5", actor="t")
        await check_proxy(db, dead, timeout=1.0)
        assert dead.status == ProxyStatus.DOWN.value and dead.last_error
        assert to_spec(p).host == "127.0.0.1" and to_spec(dead) is not None
        dead.enabled = False
        assert to_spec(dead) is None
    finally:
        server.close()
        await server.wait_closed()


async def test_proxy_validation(db):
    with pytest.raises(ValidationFailed):
        await create_proxy(db, host="x", port=70000, protocol="socks5")


async def test_queue_completed_retry_ratelimit_cancel(factory, settings):
    q = JobQueue(factory, settings)
    calls = {"ok": 0, "net": 0}

    async def ok(payload, ctx):
        calls["ok"] += 1
        return {"echo": payload}

    async def net(payload, ctx):
        calls["net"] += 1
        raise ConnectionError("boom")

    async def flood(payload, ctx):
        raise RateLimited("server says wait", wait_seconds=120)

    async def bad(payload, ctx):
        raise ValueError("not retryable")

    pool = WorkerPool(q, {"ok": ok, "net": net, "flood": flood, "bad": bad}, concurrency=1, settings=settings)
    j_ok = await q.enqueue("ok", {"x": 1}, requested_by="t")
    j_net = await q.enqueue("net", {})
    j_flood = await q.enqueue("flood", {})
    j_bad = await q.enqueue("bad", {})
    j_cancel = await q.enqueue("ok", {})
    assert await q.cancel(j_cancel.id)
    for _ in range(4):
        await pool.run_once()
    ok_job = await q.get(j_ok.id)
    assert ok_job.status == ExecutionStatus.COMPLETED.value and calls["ok"] == 1 and '"x": 1' in ok_job.result_json
    net_job = await q.get(j_net.id)
    assert net_job.status == ExecutionStatus.RETRYING.value and net_job.attempts == 1 and net_job.not_before
    flood_job = await q.get(j_flood.id)
    assert flood_job.status == ExecutionStatus.WAITING.value and flood_job.attempts == 0
    assert flood_job.not_before > datetime.now(timezone.utc) + timedelta(seconds=100)  # server delay honoured verbatim
    bad_job = await q.get(j_bad.id)
    assert bad_job.status == ExecutionStatus.FAILED.value and bad_job.error_category == ErrorCategory.VALIDATION_ERROR.value
    assert (await q.get(j_cancel.id)).status == ExecutionStatus.CANCELLED.value
    # retries until max_attempts then FAILED
    for _ in range(6):
        await asyncio.sleep(0.35)
        await pool.run_once()
    net_job = await q.get(j_net.id)
    assert net_job.status == ExecutionStatus.FAILED.value and net_job.attempts == settings.capacity.max_retries + 1
    assert calls["net"] == settings.capacity.max_retries + 1
    stats = await q.stats()
    assert stats["COMPLETED"] == 1 and stats["FAILED"] == 2 and stats["WAITING"] == 1 and stats["CANCELLED"] == 1


async def test_queue_resume_stale_and_capacity(factory, settings, monkeypatch):
    q = JobQueue(factory, settings)
    j = await q.enqueue("ok", {})
    async with factory() as db:
        rec = await db.get(JobRecord, j.id)
        rec.status = ExecutionStatus.RUNNING.value
        rec.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.commit()
    assert await q.resume_stale_running() == 1
    assert (await q.get(j.id)).status == ExecutionStatus.PENDING.value
    monkeypatch.setattr(settings.capacity, "queue_max", 1)
    with pytest.raises(ValidationFailed):
        await q.enqueue("ok", {})


async def test_timeout_is_retryable(factory, settings, monkeypatch):
    q = JobQueue(factory, settings)

    async def slow(payload, ctx):
        await asyncio.sleep(5)

    monkeypatch.setattr(settings.capacity, "task_timeout_seconds", 1)
    pool = WorkerPool(q, {"slow": slow}, concurrency=1, settings=settings)
    j = await q.enqueue("slow", {})
    await pool.run_once()
    job = await q.get(j.id)
    assert job.status == ExecutionStatus.RETRYING.value and job.error_category == ErrorCategory.NETWORK_ERROR.value


async def test_claim_is_atomic_no_double_run(factory, settings):
    """Two workers claiming concurrently must never run the same job twice (SQLite has no SKIP LOCKED)."""
    import asyncio as _asyncio

    from app.workers.queue import JobQueue
    from app.workers.worker import WorkerPool

    q = JobQueue(factory, settings)
    runs: dict[int, int] = {}

    async def handler(payload, ctx):
        n = payload["n"]
        runs[n] = runs.get(n, 0) + 1
        await _asyncio.sleep(0.01)
        return {"n": n}

    await q.enqueue_many("count", [{"n": i} for i in range(30)])
    pool = WorkerPool(q, {"count": handler}, concurrency=2, settings=settings)
    for _ in range(60):
        done = await _asyncio.gather(pool.run_once("w0", limit=1), pool.run_once("w1", limit=1))
        if sum(done) == 0:
            break
    assert len(runs) == 30
    assert sum(runs.values()) == 30, f"jobs ran more than once: {[n for n, c in runs.items() if c > 1]}"
    assert (await q.stats())["COMPLETED"] == 30
