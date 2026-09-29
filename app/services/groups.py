"""Session groups (§5)."""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SessionGroup, TelegramSession
from app.domain.enums import AuditAction
from app.services.audit import record_audit
from app.services.errors import NotFoundError, ValidationFailed
from app.utils import dumps


async def create_group(db: AsyncSession, *, name: str, description: str | None = None, proxy_id: str | None = None,
                       check_concurrency: int | None = None, settings: dict | None = None,
                       actor: str | None = None) -> SessionGroup:
    if not name.strip():
        raise ValidationFailed("group name required")
    g = SessionGroup(name=name.strip(), description=description, proxy_id=proxy_id, check_concurrency=check_concurrency,
                     settings_json=dumps(settings) if settings else None)
    db.add(g)
    await db.flush()
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="session_group", entity_id=g.id,
                       details={"op": "create", "name": g.name})
    return g


async def get_group(db: AsyncSession, group_id: str) -> SessionGroup:
    g = await db.get(SessionGroup, group_id)
    if not g:
        raise NotFoundError("group not found")
    return g


async def list_groups(db: AsyncSession) -> list[SessionGroup]:
    return list((await db.execute(select(SessionGroup).order_by(SessionGroup.name))).scalars().all())


async def assign_sessions(db: AsyncSession, group: SessionGroup, session_ids: list[str], actor: str | None = None) -> int:
    res = await db.execute(update(TelegramSession).where(TelegramSession.id.in_(session_ids)).values(group_id=group.id))
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="session_group", entity_id=group.id,
                       details={"op": "assign", "count": res.rowcount})
    return res.rowcount


async def group_session_ids(db: AsyncSession, group: SessionGroup, enabled_only: bool = True) -> list[str]:
    q = select(TelegramSession.id).where(TelegramSession.group_id == group.id)
    if enabled_only:
        q = q.where(TelegramSession.enabled.is_(True))
    return list((await db.execute(q)).scalars().all())


async def set_group_enabled(db: AsyncSession, group: SessionGroup, enabled: bool, actor: str | None = None) -> None:
    group.enabled = enabled
    await db.execute(update(TelegramSession).where(TelegramSession.group_id == group.id).values(enabled=enabled))
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="session_group", entity_id=group.id,
                       details={"op": "enable" if enabled else "disable"})
