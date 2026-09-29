"""Audit log (§15, §18, §28). Every meaningful action goes through record_audit()."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog
from app.domain.enums import AuditAction
from app.utils import dumps


async def record_audit(db: AsyncSession, action: AuditAction | str, *, actor: str | None = None,
                       entity_type: str | None = None, entity_id: str | None = None, session_id: str | None = None,
                       case_id: str | None = None, provider: str | None = None, result: str | None = None,
                       reason: str | None = None, details: dict | None = None, ip: str | None = None) -> AuditLog:
    log = AuditLog(action=str(getattr(action, "value", action)), actor=actor, entity_type=entity_type,
                   entity_id=entity_id, session_id=session_id, case_id=case_id, provider=provider, result=result,
                   reason=reason, details_json=dumps(details) if details else None, ip_address=ip,
                   at=datetime.now(timezone.utc))
    db.add(log)
    return log


async def search_audit(db: AsyncSession, *, action: str | None = None, actor: str | None = None,
                       entity_id: str | None = None, case_id: str | None = None, session_id: str | None = None,
                       since: datetime | None = None, until: datetime | None = None, limit: int = 200,
                       offset: int = 0) -> tuple[list[AuditLog], int]:
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action == action)
    if actor:
        q = q.where(AuditLog.actor == actor)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    if case_id:
        q = q.where(AuditLog.case_id == case_id)
    if session_id:
        q = q.where(AuditLog.session_id == session_id)
    if since:
        q = q.where(AuditLog.at >= since)
    if until:
        q = q.where(AuditLog.at <= until)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(AuditLog.at.desc()).limit(limit).offset(offset))).scalars().all()
    return list(rows), total
