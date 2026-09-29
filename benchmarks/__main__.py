"""Application capacity benchmark (§36, §37, §39).

Measures THIS APPLICATION's ability to handle N session files (file handling, memory, database, concurrency, queue,
dashboard responsiveness) using synthetic session files and the simulation provider. It says nothing about whether
real accounts stay available on Telegram; that is a platform/account matter and is out of scope.

Usage:  python -m benchmarks [--sizes 10,25,50,100,200,500] [--concurrency 8] [--db <url>]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil


def _configure(db_url: str | None, root: Path) -> None:
    os.environ.update({
        "SESSIONS_ROOT": str(root / "sessions"), "EVIDENCE_ROOT": str(root / "evidence"),
        "BACKUP_ROOT": str(root / "backups"), "EXPORT_ROOT": str(root / "exports"),
        "TELEGRAM_PROVIDER": "simulation", "WORKERS_ENABLED": "false", "APP_ENV": "local",
        "APP_SECRET_KEY": "benchmark-secret-key-benchmark-secret-key",
    })
    if db_url:
        os.environ["DATABASE_URL"] = db_url


async def run_size(n: int, concurrency: int) -> dict:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.config import get_settings
    from app.db.engine import get_engine, get_session_factory
    from app.db.models import Base, User
    from app.security.auth import create_access_token, hash_password
    from app.services.sessions import check_many, discover_sessions
    from app.telegram.session_files import create_synthetic_session_file
    from app.workers.handlers import HANDLERS
    from app.workers.queue import JobQueue
    from app.workers.worker import WorkerPool

    settings = get_settings()
    settings.ensure_dirs()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = get_session_factory()
    proc = psutil.Process()
    active = settings.sessions_root / "active"
    shutil.rmtree(active, ignore_errors=True)
    active.mkdir(parents=True)
    for sub in ("disabled", "quarantined"):
        shutil.rmtree(settings.sessions_root / sub, ignore_errors=True)
        (settings.sessions_root / sub).mkdir(parents=True)

    res: dict = {"sessions": n, "concurrency": concurrency}
    rss0 = proc.memory_info().rss
    t0 = time.perf_counter()
    for i in range(n):
        tag = "banned" if i % 25 == 24 else "flood" if i % 40 == 39 else "netfail" if i % 33 == 32 else ""
        create_synthetic_session_file(active / f"bench_{i:04d}{('_' + tag) if tag else ''}.session")
    res["file_generation_s"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    async with factory() as db:
        rep = await discover_sessions(db, actor="bench")
        await db.commit()
    res["discover_s"] = round(time.perf_counter() - t0, 3)
    res["discover_rows_per_s"] = round(n / max(res["discover_s"], 1e-6), 1)

    async with factory() as db:
        ids = list((await db.execute(text("SELECT id FROM sessions"))).scalars().all())
    t0 = time.perf_counter()
    rep2 = await check_many(factory, ids, concurrency=concurrency, actor="bench")
    res["check_all_s"] = round(time.perf_counter() - t0, 3)
    res["checks_per_s"] = round(n / max(res["check_all_s"], 1e-6), 1)
    res["check_summary"] = {"succeeded": rep2.succeeded, "failed": rep2.failed, "waiting": rep2.waiting, "errors": len(rep2.errors)}

    queue = JobQueue(factory, settings)
    t0 = time.perf_counter()
    await queue.enqueue_many("session.check", [{"session_id": sid} for sid in ids], requested_by="bench")
    res["enqueue_s"] = round(time.perf_counter() - t0, 3)
    pool = WorkerPool(queue, HANDLERS, concurrency=concurrency, settings=settings, name="bench")
    t0 = time.perf_counter()
    processed = 0
    while True:
        done = await asyncio.gather(*(pool.run_once(f"bench-{i}", limit=1) for i in range(concurrency)))
        processed += sum(done)
        if sum(done) == 0:
            break
    res["queue_drain_s"] = round(time.perf_counter() - t0, 3)
    res["queue_jobs_per_s"] = round(processed / max(res["queue_drain_s"], 1e-6), 1)
    res["queue_stats"] = await queue.stats()

    async with factory() as db:
        db.add(User(username="bench", password_hash=hash_password("bench-pass-123"), role="admin"))
        await db.commit()
        user = (await db.execute(text("SELECT id FROM users"))).scalar_one()
    from app.db.models import User as U
    from app.main import app

    async with factory() as db:
        u = await db.get(U, user)
        headers = {"Authorization": f"Bearer {create_access_token(u)}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://bench") as client:
        timings = {}
        for name, path in [("api_list_100", "/api/v1/sessions?limit=100"), ("api_stats", "/api/v1/sessions/stats"),
                           ("api_health_all", "/api/v1/sessions/health"), ("api_analytics", "/api/v1/monitoring/analytics")]:
            t0 = time.perf_counter()
            r = await client.get(path, headers=headers)
            assert r.status_code == 200, (path, r.text[:200])
            timings[name + "_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        client.cookies.set("tg_access", headers["Authorization"].split()[1])
        for name, path in [("web_sessions_page", "/sessions"), ("web_dashboard", "/")]:
            t0 = time.perf_counter()
            r = await client.get(path)
            assert r.status_code == 200, path
            timings[name + "_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    res["dashboard"] = timings
    res["rss_delta_mb"] = round((proc.memory_info().rss - rss0) / 2**20, 1)
    res["rss_peak_mb"] = round(proc.memory_info().rss / 2**20, 1)
    async with factory() as db:
        res["db_rows"] = {t: (await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one()
                          for t in ("sessions", "session_checks", "audit_logs", "errors", "jobs")}
    return res


async def main_async(sizes: list[int], concurrency: int, db_url: str | None, out: Path | None) -> None:
    from rich.console import Console
    from rich.table import Table

    root = Path(tempfile.mkdtemp(prefix="tglegal_bench_"))
    _configure(db_url, root)
    from app.config import get_settings

    get_settings.cache_clear()
    console = Console()
    results = []
    for n in sizes:
        console.print(f"[bold]benchmark[/bold] sessions={n} concurrency={concurrency}")
        results.append(await run_size(n, concurrency))
    t = Table(title="Application capacity benchmark (simulation provider, synthetic files)")
    for c in ("sessions", "discover_s", "check_all_s", "checks/s", "enqueue_s", "queue_drain_s", "jobs/s",
              "api_list_100 ms", "web_sessions ms", "rss_delta_mb"):
        t.add_column(c)
    for r in results:
        t.add_row(str(r["sessions"]), str(r["discover_s"]), str(r["check_all_s"]), str(r["checks_per_s"]), str(r["enqueue_s"]),
                  str(r["queue_drain_s"]), str(r["queue_jobs_per_s"]), str(r["dashboard"]["api_list_100_ms"]),
                  str(r["dashboard"]["web_sessions_page_ms"]), str(r["rss_delta_mb"]))
    console.print(t)
    console.print("[dim]Application capacity only. Account/platform availability is not measured or implied (§37).[/dim]")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "python": sys.version,
                                   "concurrency": concurrency, "results": results}, indent=2))
        console.print(f"written {out}")
    shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default="10,25,50,100,200,500")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--db", default=os.environ.get("BENCH_DATABASE_URL"), help="database URL (a scratch DB; tables are dropped)")
    ap.add_argument("--out", default="benchmarks/results/latest.json")
    a = ap.parse_args()
    asyncio.run(main_async([int(x) for x in a.sizes.split(",") if x], a.concurrency, a.db, Path(a.out) if a.out else None))


if __name__ == "__main__":
    main()
