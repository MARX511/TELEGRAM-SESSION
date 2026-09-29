"""Explanation templates (§11). Rendered with a sandboxed Jinja2 environment; the operator edits the result
before approval (Case.draft_json)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Case, Evidence, ExplanationTemplate, Reason, Target
from app.domain.enums import AuditAction
from app.services.audit import record_audit
from app.services.errors import NotFoundError, ValidationFailed

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "templates_seed.json"
PACKAGE_FIELDS = ("subject", "summary", "reason", "evidence_summary", "requested_review", "reference", "additional_notes")
_env = SandboxedEnvironment(autoescape=False, trim_blocks=True, lstrip_blocks=True)


async def seed_templates(db: AsyncSession, actor: str | None = None) -> int:
    existing = {t.name for t in (await db.execute(select(ExplanationTemplate))).scalars().all()}
    added = 0
    for item in json.loads(SEED_PATH.read_text(encoding="utf-8")):
        if item["name"] in existing:
            continue
        db.add(ExplanationTemplate(**item))
        added += 1
    if added:
        await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="template",
                           details={"op": "seed", "added": added})
    return added


async def list_templates(db: AsyncSession, active_only: bool = True) -> list[ExplanationTemplate]:
    q = select(ExplanationTemplate).order_by(ExplanationTemplate.name)
    if active_only:
        q = q.where(ExplanationTemplate.active.is_(True))
    return list((await db.execute(q)).scalars().all())


async def get_template(db: AsyncSession, template_id: str | None = None, name: str | None = None,
                       reason_code: str | None = None) -> ExplanationTemplate:
    if template_id:
        t = await db.get(ExplanationTemplate, template_id)
    elif name:
        t = (await db.execute(select(ExplanationTemplate).where(ExplanationTemplate.name == name))).scalar_one_or_none()
    else:
        t = None
        if reason_code:
            t = (await db.execute(select(ExplanationTemplate).where(ExplanationTemplate.reason_code == reason_code,
                                                                     ExplanationTemplate.active.is_(True)))).scalars().first()
        if t is None:
            t = (await db.execute(select(ExplanationTemplate).where(ExplanationTemplate.name == "default"))).scalar_one_or_none()
    if not t:
        raise NotFoundError("template not found")
    return t


async def create_template(db: AsyncSession, actor: str | None = None, **fields) -> ExplanationTemplate:
    missing = [f for f in ("name", "subject", "summary", "reason_text", "evidence_summary", "requested_review", "reference")
               if not fields.get(f)]
    if missing:
        raise ValidationFailed(f"missing template fields: {', '.join(missing)}")
    t = ExplanationTemplate(**{k: v for k, v in fields.items() if hasattr(ExplanationTemplate, k)})
    db.add(t)
    await db.flush()
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="template", entity_id=t.id,
                       details={"op": "create", "name": t.name})
    return t


def _ctx_obj(model) -> dict:
    if model is None:
        return {}
    return {c.key: getattr(model, c.key) for c in model.__table__.columns}


def render_package(template: ExplanationTemplate, case: Case, target: Target, reason: Reason | None,
                   evidence: list[Evidence]) -> dict:
    ctx = {"case": _ctx_obj(case), "target": _ctx_obj(target), "reason": _ctx_obj(reason) if reason else None,
           "evidence": [_ctx_obj(e) for e in evidence], "now": datetime.now(timezone.utc).isoformat()}
    # reason.default_explanation_template may itself contain Jinja expressions -> render it first
    if reason and reason.default_explanation_template:
        ctx["reason"]["default_explanation_template"] = _env.from_string(reason.default_explanation_template).render(ctx)
    src = {"subject": template.subject, "summary": template.summary, "reason": template.reason_text,
           "evidence_summary": template.evidence_summary, "requested_review": template.requested_review,
           "reference": template.reference, "additional_notes": template.additional_notes or ""}
    return {k: _env.from_string(v).render(ctx).strip() for k, v in src.items()}
