"""System / application monitoring (§32)."""
from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta, timezone

import psutil
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import ErrorRecord, JobRecord, SessionCheck, Submission, TelegramSession
from app.domain.enums import ExecutionStatus

_START = time.time()


def system_metrics(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    vm = psutil.virtual_memory()
    du = shutil.disk_usage(str(settings.sessions_root if settings.sessions_root.exists() else "."))
    proc = psutil.Process()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None), "cpu_count": psutil.cpu_count(),
        "ram": {"total_mb": vm.total // 2**20, "used_mb": vm.used // 2**20, "percent": vm.percent},
        "process": {"rss_mb": proc.memory_info().rss // 2**20, "threads": proc.num_threads()},
        "disk": {"total_gb": round(du.total / 2**30, 1), "free_gb": round(du.free / 2**30, 1),
                 "percent_used": round(du.used / du.total * 100, 1) if du.total else 0},
        "uptime_seconds": int(time.time() - _START),
    }


async def database_metrics(db: AsyncSession) -> dict:
    t0 = time.perf_counter()
    await db.execute(text("SELECT 1"))
    ping_ms = int((time.perf_counter() - t0) * 1000)
    bind = db.get_bind()
    pool = getattr(bind, "pool", None)
    pool_info = {}
    for attr in ("size", "checkedin", "checkedout", "overflow"):
        fn = getattr(pool, attr, None)
        if callable(fn):
            try:
                pool_info[attr] = fn()
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, "ping_ms": ping_ms, "dialect": bind.dialect.name, "pool": pool_info}


async def queue_metrics(db: AsyncSession) -> dict:
    rows = (await db.execute(select(JobRecord.status, func.count()).group_by(JobRecord.status))).all()
    out = {s.value: 0 for s in ExecutionStatus}
    out.update({r[0]: r[1] for r in rows})
    return out


async def activity_metrics(db: AsyncSession, hours: int = 24) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    checks = (await db.execute(select(func.count(SessionCheck.id), func.sum(sa_bool(SessionCheck.success)))
                               .where(SessionCheck.started_at >= since))).one()
    subs = (await db.execute(select(Submission.status, func.count()).where(Submission.created_at >= since)
                             .group_by(Submission.status))).all()
    errs = (await db.execute(select(ErrorRecord.category, func.count()).where(ErrorRecord.occurred_at >= since)
                             .group_by(ErrorRecord.category))).all()
    total_sessions = (await db.execute(select(func.count(TelegramSession.id)))).scalar_one()
    return {"window_hours": hours, "session_checks": {"total": checks[0] or 0, "succeeded": int(checks[1] or 0)},
            "submissions": {r[0]: r[1] for r in subs}, "errors": {r[0]: r[1] for r in errs},
            "sessions_registered": total_sessions}


def sa_bool(col):
    from sqlalchemy import case as sa_case

    return sa_case((col.is_(True), 1), else_=0)


async def full_snapshot(db: AsyncSession, settings: Settings | None = None) -> dict:
    return {"system": system_metrics(settings), "database": await database_metrics(db), "queue": await queue_metrics(db),
            "activity": await activity_metrics(db), "capacity": (settings or get_settings()).capacity.model_dump()}
