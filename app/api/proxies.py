from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.db.models import User
from app.schemas import ProxyCreate, ProxyOut
from app.security import rbac
from app.services import proxies as svc

router = APIRouter(prefix="/proxies", tags=["proxies"])


@router.get("", response_model=list[ProxyOut])
async def list_proxies(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_PROXIES_READ))):
    return await svc.list_proxies(db)


@router.post("", response_model=ProxyOut, status_code=201)
async def create(body: ProxyCreate, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_PROXIES_WRITE))):
    return await svc.create_proxy(db, actor=user.username, **body.model_dump())


@router.post("/{proxy_id}/check", response_model=ProxyOut)
async def check(proxy_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_PROXIES_WRITE))):
    return await svc.check_proxy(db, await svc.get_proxy(db, proxy_id), actor=user.username)


@router.post("/{proxy_id}/enable", response_model=ProxyOut)
async def enable(proxy_id: str, enabled: bool = True, db: AsyncSession = Depends(get_db),
                 _: User = Depends(rbac.require(rbac.P_PROXIES_WRITE))):
    p = await svc.get_proxy(db, proxy_id)
    p.enabled = enabled
    return p
