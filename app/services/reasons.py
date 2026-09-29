"""Reason catalog (§10): data-driven, versioned, editable. Seeded from app/data/reasons_seed.json."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Reason
from app.domain.enums import AuditAction
from app.services.audit import record_audit
from app.services.errors import NotFoundError, ValidationFailed

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "reasons_seed.json"


async def seed_reasons(db: AsyncSession, actor: str | None = None) -> int:
    existing = {r.code for r in (await db.execute(select(Reason))).scalars().all()}
    added = 0
    for item in json.loads(SEED_PATH.read_text(encoding="utf-8")):
        if item["code"] in existing:
            continue
        db.add(Reason(**item, active=True, version=1))
        added += 1
    if added:
        await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="reason",
                           details={"op": "seed", "added": added})
    return added


async def list_reasons(db: AsyncSession, active_only: bool = True) -> list[Reason]:
    q = select(Reason).order_by(Reason.code, Reason.version.desc())
    if active_only:
        q = q.where(Reason.active.is_(True))
    return list((await db.execute(q)).scalars().all())


async def get_reason_by_code(db: AsyncSession, code: str) -> Reason:
    r = (await db.execute(select(Reason).where(Reason.code == code, Reason.active.is_(True))
                          .order_by(Reason.version.desc()).limit(1))).scalar_one_or_none()
    if not r:
        raise NotFoundError(f"reason '{code}' not found")
    return r


async def create_reason(db: AsyncSession, *, code: str, name: str, description: str | None = None,
                        policy_reference: str | None = None, default_explanation_template: str | None = None,
                        official_channel_hint: str | None = None, actor: str | None = None) -> Reason:
    if not code or not name:
        raise ValidationFailed("code and name are required")
    exists = (await db.execute(select(Reason).where(Reason.code == code))).scalars().first()
    if exists:
        raise ValidationFailed(f"reason '{code}' exists; use update to create a new version")
    r = Reason(code=code, name=name, description=description, policy_reference=policy_reference,
               default_explanation_template=default_explanation_template, official_channel_hint=official_channel_hint)
    db.add(r)
    await db.flush()
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="reason", entity_id=r.id,
                       details={"op": "create", "code": code})
    return r


async def update_reason(db: AsyncSession, code: str, actor: str | None = None, **fields) -> Reason:
    """Creates a new version and deactivates the previous one, keeping history."""
    current = await get_reason_by_code(db, code)
    data = {"name": current.name, "description": current.description, "policy_reference": current.policy_reference,
            "default_explanation_template": current.default_explanation_template,
            "official_channel_hint": current.official_channel_hint}
    data.update({k: v for k, v in fields.items() if v is not None and k in data})
    current.active = False
    nr = Reason(code=code, version=current.version + 1, active=True, **data)
    db.add(nr)
    await db.flush()
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="reason", entity_id=nr.id,
                       details={"op": "new_version", "code": code, "version": nr.version})
    return nr


async def deactivate_reason(db: AsyncSession, code: str, actor: str | None = None) -> None:
    r = await get_reason_by_code(db, code)
    r.active = False
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="reason", entity_id=r.id,
                       details={"op": "deactivate", "code": code})
