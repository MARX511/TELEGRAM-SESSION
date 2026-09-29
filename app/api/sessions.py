from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_queue, get_session_factory
from app.config import get_settings
from app.db.models import Account, SessionCheck, TelegramSession, User
from app.domain.enums import SessionLocation
from app.schemas import (AccountOut, BulkCheckIn, GroupCreate, GroupOut, Page, SessionCheckOut, SessionOut,
                         SessionUpdate)
from app.security import rbac
from app.security.session_crypto import SessionCrypto
from app.services import groups as group_service
from app.services import sessions as svc
from app.services.errors import ValidationFailed
from app.workers.queue import JobQueue

router = APIRouter(prefix="/sessions", tags=["sessions"])
groups_router = APIRouter(prefix="/session-groups", tags=["session-groups"])
accounts_router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=Page)
async def list_sessions(status: str | None = None, health: str | None = None, location: str | None = None,
                        group_id: str | None = None, search: str | None = None, sort: str = "file_name",
                        order: str = "asc", limit: int = Query(100, le=1000), offset: int = 0,
                        db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_SESSIONS_READ))):
    rows, total = await svc.list_sessions(db, status=status, health=health, location=location, group_id=group_id,
                                          search=search, sort=sort, order=order, limit=limit, offset=offset)
    return Page(total=total, limit=limit, offset=offset, items=[SessionOut.model_validate(r) for r in rows])


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_SESSIONS_READ))):
    return await svc.session_stats(db)


@router.get("/health")
async def health_all(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_SESSIONS_READ))):
    rows, _t = await svc.list_sessions(db, limit=10000)
    return [svc.health_snapshot(r) for r in rows]


@router.post("/scan")
async def scan(db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    rep = await svc.discover_sessions(db, actor=user.username)
    return {"discovered": rep.discovered, "new": rep.new, "updated": rep.updated, "missing": rep.missing}


@router.post("/check")
async def bulk_check(body: BulkCheckIn, db: AsyncSession = Depends(get_db), queue: JobQueue = Depends(get_queue),
                     user: User = Depends(rbac.require(rbac.P_SESSIONS_CHECK))):
    """Bulk health check (§23/§27). Administrative: validates the operator's own sessions, bounded by capacity."""
    ids = body.session_ids
    if not ids:
        rows, _t = await svc.list_sessions(db, status=body.status, group_id=body.group_id, location=body.location, limit=100000)
        ids = [r.id for r in rows if r.enabled]
    if body.inline:
        await db.commit()
        rep = await svc.check_many(get_session_factory(), ids, actor=user.username)
        return {"mode": "inline", **rep.__dict__}
    jobs = [await queue.enqueue("session.check", {"session_id": sid}, requested_by=user.username, db=db) for sid in ids]
    return {"mode": "queued", "jobs": len(jobs)}


@router.get("/{session_id}", response_model=SessionOut)
async def get_one(session_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_SESSIONS_READ))):
    return await svc.get_session(db, session_id)


@router.patch("/{session_id}", response_model=SessionOut)
async def update_one(session_id: str, body: SessionUpdate, db: AsyncSession = Depends(get_db),
                     _: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    s = await svc.get_session(db, session_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    return s


@router.get("/{session_id}/health")
async def health_one(session_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_SESSIONS_READ))):
    return svc.health_snapshot(await svc.get_session(db, session_id))


@router.get("/{session_id}/checks", response_model=list[SessionCheckOut])
async def checks(session_id: str, limit: int = 50, db: AsyncSession = Depends(get_db),
                 _: User = Depends(rbac.require(rbac.P_SESSIONS_READ))):
    rows = (await db.execute(select(SessionCheck).where(SessionCheck.session_id == session_id)
                             .order_by(SessionCheck.started_at.desc()).limit(limit))).scalars().all()
    return list(rows)


@router.post("/{session_id}/check", response_model=SessionCheckOut)
async def check_one(session_id: str, force: bool = False, db: AsyncSession = Depends(get_db),
                    user: User = Depends(rbac.require(rbac.P_SESSIONS_CHECK))):
    s = await svc.get_session(db, session_id)
    return await svc.check_session(db, s, actor=user.username, force=force)


@router.post("/{session_id}/disable", response_model=SessionOut)
async def disable(session_id: str, reason: str | None = None, db: AsyncSession = Depends(get_db),
                  user: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    return await svc.disable_session(db, await svc.get_session(db, session_id), actor=user.username, reason=reason)


@router.post("/{session_id}/enable", response_model=SessionOut)
async def enable(session_id: str, reason: str | None = None, db: AsyncSession = Depends(get_db),
                 user: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    return await svc.enable_session(db, await svc.get_session(db, session_id), actor=user.username, reason=reason)


@router.post("/{session_id}/quarantine", response_model=SessionOut)
async def quarantine(session_id: str, reason: str | None = None, db: AsyncSession = Depends(get_db),
                     user: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    return await svc.quarantine_session(db, await svc.get_session(db, session_id), actor=user.username, reason=reason)


@router.post("/{session_id}/encrypt", response_model=SessionOut)
async def encrypt(session_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    crypto = SessionCrypto(get_settings().session_file_encryption_key)
    if not crypto.enabled:
        raise ValidationFailed("SESSION_FILE_ENCRYPTION_KEY is not configured")
    return await svc.encrypt_session_file(db, await svc.get_session(db, session_id), crypto, actor=user.username)


# ------------------------------------------------------------------------- groups
@groups_router.get("", response_model=list[GroupOut])
async def list_groups(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_SESSIONS_READ))):
    return await group_service.list_groups(db)


@groups_router.post("", response_model=GroupOut, status_code=201)
async def create_group(body: GroupCreate, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    return await group_service.create_group(db, actor=user.username, **body.model_dump())


@groups_router.post("/{group_id}/assign")
async def assign(group_id: str, session_ids: list[str], db: AsyncSession = Depends(get_db),
                 user: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    g = await group_service.get_group(db, group_id)
    return {"assigned": await group_service.assign_sessions(db, g, session_ids, actor=user.username)}


@groups_router.post("/{group_id}/check")
async def check_group(group_id: str, db: AsyncSession = Depends(get_db), queue: JobQueue = Depends(get_queue),
                      user: User = Depends(rbac.require(rbac.P_SESSIONS_CHECK))):
    g = await group_service.get_group(db, group_id)
    ids = await group_service.group_session_ids(db, g)
    for sid in ids:
        await queue.enqueue("session.check", {"session_id": sid}, requested_by=user.username, db=db)
    return {"queued": len(ids)}


@groups_router.post("/{group_id}/enable")
async def enable_group(group_id: str, enabled: bool = True, db: AsyncSession = Depends(get_db),
                       user: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    g = await group_service.get_group(db, group_id)
    await group_service.set_group_enabled(db, g, enabled, actor=user.username)
    return {"enabled": enabled}


# ------------------------------------------------------------------------- accounts
@accounts_router.get("", response_model=list[AccountOut])
async def list_accounts(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_SESSIONS_READ))):
    return list((await db.execute(select(Account).order_by(Account.created_at.desc()))).scalars().all())
