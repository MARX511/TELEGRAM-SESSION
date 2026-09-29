"""Job handlers. Imported lazily by the app factory / CLI to avoid import cycles."""
from __future__ import annotations

from app.config import get_settings
from app.db.engine import get_session_factory
from app.workers.worker import Handler, JobContext


async def session_check(payload: dict, ctx: JobContext) -> dict:
    from app.db.models import TelegramSession
    from app.services.errors import NotFoundError
    from app.services.sessions import check_session

    async with get_session_factory()() as db:
        s = await db.get(TelegramSession, payload["session_id"])
        if s is None:
            raise NotFoundError("session not found")
        chk = await check_session(db, s, actor=ctx.requested_by)
        await db.commit()
        return {"status": chk.result_status, "success": chk.success, "latency_ms": chk.latency_ms}


async def sessions_discover(payload: dict, ctx: JobContext) -> dict:
    from app.services.sessions import discover_sessions

    async with get_session_factory()() as db:
        rep = await discover_sessions(db, actor=ctx.requested_by)
        await db.commit()
        return {"discovered": rep.discovered, "new": rep.new, "updated": rep.updated, "missing": rep.missing}


async def proxy_check(payload: dict, ctx: JobContext) -> dict:
    from app.services.proxies import check_proxy, get_proxy

    async with get_session_factory()() as db:
        p = await get_proxy(db, payload["proxy_id"])
        await check_proxy(db, p, actor=ctx.requested_by)
        await db.commit()
        return {"status": p.status, "latency_ms": p.latency_ms}


async def export_create(payload: dict, ctx: JobContext) -> dict:
    from app.services.exports import export_records

    async with get_session_factory()() as db:
        rec = await export_records(db, payload["export_type"], payload.get("fmt", "json"),
                                   filters=payload.get("filters"), actor=ctx.requested_by)
        await db.commit()
        return {"export_id": rec.id, "file_path": rec.file_path}


async def backup_create(payload: dict, ctx: JobContext) -> dict:
    from app.backup.manager import BackupManager

    async with get_session_factory()() as db:
        rec = await BackupManager(get_settings()).backup(db, payload.get("backup_type", "database"),
                                                        actor=ctx.requested_by)
        await db.commit()
        return {"backup_id": rec.id, "file_path": rec.file_path}


HANDLERS: dict[str, Handler] = {
    "session.check": session_check,
    "sessions.discover": sessions_discover,
    "proxy.check": proxy_check,
    "export.create": export_create,
    "backup.create": backup_create,
}
