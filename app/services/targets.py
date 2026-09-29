"""Target registry (§8)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Case, Target
from app.domain.enums import AuditAction, CaseStatus, TargetStatus, TargetType
from app.services.audit import record_audit
from app.services.errors import NotFoundError, ValidationFailed

_TME = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(?P<path>.+)$", re.I)


def normalize_target(target_type: str, url: str | None, username: str | None, telegram_id: int | None) -> dict:
    """Best-effort normalisation of t.me links / @usernames. Returns fields to store."""
    out = {"target_type": target_type, "url": url.strip() if url else None,
           "username": username.strip().lstrip("@").lower() if username else None, "telegram_id": telegram_id}
    if out["url"]:
        m = _TME.match(out["url"])
        if m:
            path = m.group("path").strip("/")
            parts = path.split("/")
            if parts and parts[0] not in ("c", "joinchat", "+", "s") and not parts[0].startswith("+"):
                out["username"] = out["username"] or parts[0].lower()
                if len(parts) >= 2 and parts[1].isdigit() and target_type in (TargetType.MESSAGE.value,
                                                                              TargetType.MESSAGE_REFERENCE.value):
                    out.setdefault("message_id", int(parts[1]))
        elif not urlparse(out["url"]).scheme:
            out["url"] = "https://" + out["url"]
    return out


async def create_target(db: AsyncSession, *, target_type: str, url: str | None = None, username: str | None = None,
                        telegram_id: int | None = None, title: str | None = None, description: str | None = None,
                        tags: str | None = None, actor: str | None = None) -> Target:
    if target_type not in {t.value for t in TargetType}:
        raise ValidationFailed(f"unknown target_type {target_type}")
    if not any((url, username, telegram_id)):
        raise ValidationFailed("one of url / username / telegram_id is required")
    n = normalize_target(target_type, url, username, telegram_id)
    t = Target(target_type=target_type, url=n["url"], username=n["username"], telegram_id=n["telegram_id"], title=title,
               description=description, tags=tags, created_by=actor)
    db.add(t)
    await db.flush()
    await record_audit(db, AuditAction.TARGET_ADDED, actor=actor, entity_type="target", entity_id=t.id,
                       details={"type": target_type, "username": t.username, "url": t.url})
    return t


async def get_target(db: AsyncSession, target_id: str) -> Target:
    t = await db.get(Target, target_id)
    if not t:
        raise NotFoundError("target not found")
    return t


async def update_target(db: AsyncSession, t: Target, actor: str | None = None, **fields) -> Target:
    for k, v in fields.items():
        if v is not None and hasattr(t, k):
            setattr(t, k, v)
    await record_audit(db, AuditAction.TARGET_UPDATED, actor=actor, entity_type="target", entity_id=t.id,
                       details={k: v for k, v in fields.items() if v is not None})
    return t


async def list_targets(db: AsyncSession, *, target_type: str | None = None, status: str | None = None,
                       search: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[Target], int]:
    q = select(Target)
    if target_type:
        q = q.where(Target.target_type == target_type)
    if status:
        q = q.where(Target.status == status)
    if search:
        like = f"%{search.lstrip('@')}%"
        conds = [Target.url.ilike(like), Target.username.ilike(like), Target.title.ilike(like), Target.tags.ilike(like)]
        if search.isdigit():
            conds.append(Target.telegram_id == int(search))
        q = q.where(or_(*conds))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Target.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return list(rows), total


async def target_stats(db: AsyncSession) -> dict:
    total = (await db.execute(select(func.count(Target.id)))).scalar_one()
    by_type = {r[0]: r[1] for r in (await db.execute(select(Target.target_type, func.count()).group_by(Target.target_type))).all()}
    open_cases = (await db.execute(select(func.count(Case.id)).where(Case.status != CaseStatus.CLOSED.value))).scalar_one()
    closed_cases = (await db.execute(select(func.count(Case.id)).where(Case.status == CaseStatus.CLOSED.value))).scalar_one()
    return {"total": total, "accounts": by_type.get("account", 0), "channels": by_type.get("channel", 0),
            "groups": by_type.get("group", 0), "messages": by_type.get("message", 0) + by_type.get("message_reference", 0),
            "urls": by_type.get("url", 0), "usernames": by_type.get("username", 0), "by_type": by_type,
            "open_cases": open_cases, "closed_cases": closed_cases}
