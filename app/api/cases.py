from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.db.models import User
from app.schemas import (CaseCreate, CaseEventOut, CaseOut, CaseUpdate, DraftEdit, Page, ReasonIn, ReasonOut,
                         TemplateIn, TemplateOut, TransitionIn)
from app.security import rbac
from app.services import cases as svc
from app.services import reasons as reason_service
from app.services import templates as template_service
from app.utils import loads

router = APIRouter(prefix="/cases", tags=["cases"])
reasons_router = APIRouter(prefix="/reasons", tags=["reasons"])
templates_router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=Page)
async def list_cases(status: str | None = None, priority: str | None = None, target_id: str | None = None,
                     assigned_operator: str | None = None, search: str | None = None, limit: int = Query(100, le=1000),
                     offset: int = 0, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_CASES_READ))):
    rows, total = await svc.list_cases(db, status=status, priority=priority, target_id=target_id,
                                       assigned_operator=assigned_operator, search=search, limit=limit, offset=offset)
    return Page(total=total, limit=limit, offset=offset, items=[CaseOut.model_validate(r) for r in rows])


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_CASES_READ))):
    return await svc.case_stats(db)


@router.post("", response_model=CaseOut, status_code=201)
async def create(body: CaseCreate, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_CASES_WRITE))):
    return await svc.create_case(db, actor=user.username, **body.model_dump())


@router.get("/{case_id}", response_model=CaseOut)
async def get_one(case_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_CASES_READ))):
    return await svc.get_case(db, case_id)


@router.patch("/{case_id}", response_model=CaseOut)
async def update(case_id: str, body: CaseUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_CASES_WRITE))):
    return await svc.update_case(db, await svc.get_case(db, case_id), actor=user.username, **body.model_dump())


@router.post("/{case_id}/reason/{reason_code}", response_model=CaseOut)
async def set_reason(case_id: str, reason_code: str, db: AsyncSession = Depends(get_db),
                     user: User = Depends(rbac.require(rbac.P_CASES_WRITE))):
    return await svc.set_reason(db, await svc.get_case(db, case_id), reason_code, actor=user.username)


@router.post("/{case_id}/draft")
async def generate_draft(case_id: str, template_id: str | None = None, db: AsyncSession = Depends(get_db),
                         user: User = Depends(rbac.require(rbac.P_CASES_WRITE))):
    return await svc.generate_draft(db, await svc.get_case(db, case_id), template_id=template_id, actor=user.username)


@router.get("/{case_id}/draft")
async def get_draft(case_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_CASES_READ))):
    return loads((await svc.get_case(db, case_id)).draft_json) or {}


@router.put("/{case_id}/draft")
async def edit_draft(case_id: str, body: DraftEdit, db: AsyncSession = Depends(get_db),
                     user: User = Depends(rbac.require(rbac.P_CASES_WRITE))):
    return await svc.update_draft(db, await svc.get_case(db, case_id), body.model_dump(exclude_unset=True), actor=user.username)


@router.post("/{case_id}/transition", response_model=CaseOut)
async def transition(case_id: str, body: TransitionIn, db: AsyncSession = Depends(get_db),
                     user: User = Depends(rbac.require(rbac.P_CASES_WRITE))):
    if body.to_status == "Approved" and not rbac.has_permission(user, rbac.P_CASES_APPROVE):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="approval requires the reviewer/admin role")
    return await svc.transition(db, await svc.get_case(db, case_id), body.to_status, actor=user.username, note=body.note)


@router.post("/{case_id}/approve", response_model=CaseOut)
async def approve(case_id: str, note: str | None = None, db: AsyncSession = Depends(get_db),
                  user: User = Depends(rbac.require(rbac.P_CASES_APPROVE))):
    return await svc.approve(db, await svc.get_case(db, case_id), actor=user.username, note=note)


@router.get("/{case_id}/history", response_model=list[CaseEventOut])
async def history(case_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_CASES_READ))):
    return await svc.case_history(db, await svc.get_case(db, case_id))


# ---------------------------------------------------------------------- reasons
@reasons_router.get("", response_model=list[ReasonOut])
async def list_reasons(active_only: bool = True, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_CASES_READ))):
    return await reason_service.list_reasons(db, active_only=active_only)


@reasons_router.post("", response_model=ReasonOut, status_code=201)
async def create_reason(body: ReasonIn, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_SETTINGS_MANAGE))):
    return await reason_service.create_reason(db, actor=user.username, **body.model_dump())


@reasons_router.put("/{code}", response_model=ReasonOut)
async def update_reason(code: str, body: ReasonIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(rbac.require(rbac.P_SETTINGS_MANAGE))):
    return await reason_service.update_reason(db, code, actor=user.username, **body.model_dump(exclude={"code"}))


@reasons_router.post("/seed")
async def seed(db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_SETTINGS_MANAGE))):
    return {"reasons": await reason_service.seed_reasons(db, actor=user.username),
            "templates": await template_service.seed_templates(db, actor=user.username)}


# ---------------------------------------------------------------------- templates
@templates_router.get("", response_model=list[TemplateOut])
async def list_templates(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_CASES_READ))):
    return await template_service.list_templates(db)


@templates_router.post("", response_model=TemplateOut, status_code=201)
async def create_template(body: TemplateIn, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_SETTINGS_MANAGE))):
    return await template_service.create_template(db, actor=user.username, **body.model_dump())
