"""Case workflow (§9, §18, §25). State machine enforced via CASE_TRANSITIONS; every step is audited."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Case, CaseEvent, Evidence, Reason, Target
from app.domain.enums import CASE_TRANSITIONS, AuditAction, CasePriority, CaseStatus
from app.services.audit import record_audit
from app.services.errors import NotFoundError, TransitionError, ValidationFailed
from app.services.reasons import get_reason_by_code
from app.services.templates import PACKAGE_FIELDS, get_template, render_package
from app.utils import dumps, loads

EDITABLE_DRAFT_STATUSES = {CaseStatus.DRAFT.value, CaseStatus.PENDING_REVIEW.value}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _event(db: AsyncSession, case: Case, event_type: str, actor: str | None, *, from_status: str | None = None,
                 to_status: str | None = None, details: dict | None = None) -> CaseEvent:
    ev = CaseEvent(case_id=case.id, event_type=event_type, actor=actor, from_status=from_status, to_status=to_status,
                   details_json=dumps(details) if details else None, created_at=_now())
    db.add(ev)
    return ev


async def next_case_number(db: AsyncSession) -> str:
    today = _now().strftime("%Y%m%d")
    prefix = f"CASE-{today}-"
    n = (await db.execute(select(func.count(Case.id)).where(Case.case_number.like(prefix + "%")))).scalar_one()
    return f"{prefix}{n + 1:04d}"


async def create_case(db: AsyncSession, *, target_id: str, title: str, reason_code: str | None = None,
                      explanation: str | None = None, legal_basis: str | None = None,
                      priority: str = CasePriority.NORMAL.value, assigned_operator: str | None = None,
                      actor: str | None = None) -> Case:
    target = await db.get(Target, target_id)
    if not target:
        raise NotFoundError("target not found")
    if not title.strip():
        raise ValidationFailed("title is required")
    if priority not in {p.value for p in CasePriority}:
        raise ValidationFailed("invalid priority")
    reason = await get_reason_by_code(db, reason_code) if reason_code else None
    case = Case(case_number=await next_case_number(db), target_id=target.id, reason_id=reason.id if reason else None,
                title=title.strip(), explanation=explanation, legal_basis=legal_basis, priority=priority,
                assigned_operator=assigned_operator, created_by=actor, status=CaseStatus.DRAFT.value)
    db.add(case)
    await db.flush()
    await _event(db, case, AuditAction.CASE_CREATED.value, actor, to_status=case.status)
    await record_audit(db, AuditAction.CASE_CREATED, actor=actor, entity_type="case", entity_id=case.id, case_id=case.id,
                       details={"case_number": case.case_number, "target_id": target.id, "reason": reason_code})
    return case


async def get_case(db: AsyncSession, case_id: str) -> Case:
    q = select(Case).where(or_(Case.id == case_id, Case.case_number == case_id)).options(
        selectinload(Case.target), selectinload(Case.reason), selectinload(Case.events))
    case = (await db.execute(q)).scalar_one_or_none()
    if not case:
        raise NotFoundError("case not found")
    return case


async def update_case(db: AsyncSession, case: Case, actor: str | None = None, **fields) -> Case:
    if case.status == CaseStatus.CLOSED.value:
        raise TransitionError("closed cases are immutable")
    changed = {}
    for k in ("title", "explanation", "legal_basis", "priority", "assigned_operator", "reference_number"):
        v = fields.get(k)
        if v is not None and getattr(case, k) != v:
            setattr(case, k, v)
            changed[k] = v
    if changed:
        await _event(db, case, AuditAction.CASE_UPDATED.value, actor, details=changed)
        await record_audit(db, AuditAction.CASE_UPDATED, actor=actor, entity_type="case", entity_id=case.id,
                           case_id=case.id, details=changed)
    return case


async def set_reason(db: AsyncSession, case: Case, reason_code: str, actor: str | None = None) -> Case:
    if case.status not in EDITABLE_DRAFT_STATUSES:
        raise TransitionError("reason can only change while the case is in Draft / Pending Review")
    reason = await get_reason_by_code(db, reason_code)
    old = case.reason_id
    case.reason_id = reason.id
    await _event(db, case, AuditAction.REASON_CHANGED.value, actor, details={"from": old, "to": reason.id, "code": reason_code})
    await record_audit(db, AuditAction.REASON_CHANGED, actor=actor, entity_type="case", entity_id=case.id, case_id=case.id,
                       details={"reason_code": reason_code, "version": reason.version})
    return case


async def generate_draft(db: AsyncSession, case: Case, *, template_id: str | None = None,
                         actor: str | None = None) -> dict:
    if case.status not in EDITABLE_DRAFT_STATUSES:
        raise TransitionError("draft can only be generated while the case is in Draft / Pending Review")
    target = await db.get(Target, case.target_id)
    reason = await db.get(Reason, case.reason_id) if case.reason_id else None
    template = await get_template(db, template_id=template_id, reason_code=reason.code if reason else None)
    evidence = list((await db.execute(select(Evidence).where(Evidence.case_id == case.id))).scalars().all())
    package = render_package(template, case, target, reason, evidence)
    case.draft_json = dumps(package)
    case.template_id = template.id
    await _event(db, case, AuditAction.DRAFT_GENERATED.value, actor, details={"template": template.name})
    await record_audit(db, AuditAction.DRAFT_GENERATED, actor=actor, entity_type="case", entity_id=case.id, case_id=case.id,
                       details={"template": template.name, "evidence_count": len(evidence)})
    return package


async def update_draft(db: AsyncSession, case: Case, package: dict, actor: str | None = None) -> dict:
    """Operator edits the generated text before approval (§11)."""
    if case.status not in EDITABLE_DRAFT_STATUSES:
        raise TransitionError("draft is locked once the case is approved")
    current = loads(case.draft_json) or {}
    for k in PACKAGE_FIELDS:
        if k in package and package[k] is not None:
            current[k] = str(package[k])
    case.draft_json = dumps(current)
    await _event(db, case, "Draft Edited", actor, details={"fields": [k for k in PACKAGE_FIELDS if k in package]})
    await record_audit(db, AuditAction.CASE_UPDATED, actor=actor, entity_type="case", entity_id=case.id, case_id=case.id,
                       details={"op": "draft_edited"})
    return current


async def transition(db: AsyncSession, case: Case, to_status: str, *, actor: str | None = None,
                     note: str | None = None) -> Case:
    try:
        cur = CaseStatus(case.status)
        nxt = CaseStatus(to_status)
    except ValueError as exc:
        raise ValidationFailed(f"unknown status: {exc}") from exc
    if nxt not in CASE_TRANSITIONS[cur]:
        raise TransitionError(f"cannot move case from '{cur.value}' to '{nxt.value}'")
    if nxt == CaseStatus.APPROVED:
        if not case.draft_json:
            raise TransitionError("generate and review the draft before approval")
        if not case.reason_id:
            raise TransitionError("a reason must be set before approval")
        case.approved_by = actor
        case.approved_at = _now()
    if nxt == CaseStatus.CLOSED:
        case.closed_at = _now()
    case.status = nxt.value
    await _event(db, case, AuditAction.CASE_STATUS_CHANGED.value, actor, from_status=cur.value, to_status=nxt.value,
                 details={"note": note} if note else None)
    action = {CaseStatus.APPROVED: AuditAction.APPROVAL_GRANTED, CaseStatus.CLOSED: AuditAction.CASE_CLOSED}.get(
        nxt, AuditAction.CASE_STATUS_CHANGED)
    await record_audit(db, action, actor=actor, entity_type="case", entity_id=case.id, case_id=case.id,
                       result=nxt.value, reason=note, details={"from": cur.value, "to": nxt.value})
    return case


async def submit_for_review(db, case, actor=None, note=None):
    return await transition(db, case, CaseStatus.PENDING_REVIEW.value, actor=actor, note=note)


async def approve(db, case, actor=None, note=None):
    return await transition(db, case, CaseStatus.APPROVED.value, actor=actor, note=note)


async def mark_ready(db, case, actor=None, note=None):
    return await transition(db, case, CaseStatus.READY.value, actor=actor, note=note)


async def close_case(db, case, actor=None, note=None):
    return await transition(db, case, CaseStatus.CLOSED.value, actor=actor, note=note)


async def list_cases(db: AsyncSession, *, status: str | None = None, priority: str | None = None,
                     target_id: str | None = None, assigned_operator: str | None = None, search: str | None = None,
                     limit: int = 100, offset: int = 0) -> tuple[list[Case], int]:
    q = select(Case).options(selectinload(Case.target), selectinload(Case.reason))
    if status:
        q = q.where(Case.status == status)
    if priority:
        q = q.where(Case.priority == priority)
    if target_id:
        q = q.where(Case.target_id == target_id)
    if assigned_operator:
        q = q.where(Case.assigned_operator == assigned_operator)
    if search:
        like = f"%{search}%"
        q = q.where(or_(Case.case_number.ilike(like), Case.title.ilike(like), Case.reference_number.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Case.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return list(rows), total


async def case_stats(db: AsyncSession) -> dict:
    rows = (await db.execute(select(Case.status, func.count()).group_by(Case.status))).all()
    counts = {s.value: 0 for s in CaseStatus}
    counts.update({r[0]: r[1] for r in rows})
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


async def case_history(db: AsyncSession, case: Case) -> list[CaseEvent]:
    return list((await db.execute(select(CaseEvent).where(CaseEvent.case_id == case.id)
                                  .order_by(CaseEvent.created_at))).scalars().all())
