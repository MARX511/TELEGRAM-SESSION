from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.db.models import User
from app.schemas import Page, TargetCreate, TargetOut, TargetUpdate
from app.security import rbac
from app.services import targets as svc

router = APIRouter(prefix="/targets", tags=["targets"])


@router.get("", response_model=Page)
async def list_targets(target_type: str | None = None, status: str | None = None, search: str | None = None,
                       limit: int = Query(100, le=1000), offset: int = 0, db: AsyncSession = Depends(get_db),
                       _: User = Depends(rbac.require(rbac.P_TARGETS_READ))):
    rows, total = await svc.list_targets(db, target_type=target_type, status=status, search=search, limit=limit, offset=offset)
    return Page(total=total, limit=limit, offset=offset, items=[TargetOut.model_validate(r) for r in rows])


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_TARGETS_READ))):
    return await svc.target_stats(db)


@router.post("", response_model=TargetOut, status_code=201)
async def create(body: TargetCreate, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_TARGETS_WRITE))):
    return await svc.create_target(db, actor=user.username, **body.model_dump())


@router.get("/{target_id}", response_model=TargetOut)
async def get_one(target_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_TARGETS_READ))):
    return await svc.get_target(db, target_id)


@router.patch("/{target_id}", response_model=TargetOut)
async def update(target_id: str, body: TargetUpdate, db: AsyncSession = Depends(get_db),
                 user: User = Depends(rbac.require(rbac.P_TARGETS_WRITE))):
    return await svc.update_target(db, await svc.get_target(db, target_id), actor=user.username, **body.model_dump())
