from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db
from app.db.models import User
from app.domain.enums import AuditAction, Role
from app.schemas import LoginIn, TokenOut, UserCreate, UserOut
from app.security import rbac
from app.security.auth import authenticate, create_access_token, get_current_user, hash_password
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])


async def _login(db: AsyncSession, request: Request, username: str, password: str) -> str:
    user = await authenticate(db, username, password)
    if not user:
        await record_audit(db, AuditAction.LOGIN_FAILED, actor=username, ip=client_ip(request), result="FAILED")
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    await record_audit(db, AuditAction.LOGIN, actor=user.username, ip=client_ip(request), result="OK")
    return create_access_token(user)


@router.post("/token", response_model=TokenOut)
async def token(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    return TokenOut(access_token=await _login(db, request, form.username, form.password))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    return TokenOut(access_token=await _login(db, request, body.username, body.password))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_USERS_MANAGE))):
    return list((await db.execute(select(User).order_by(User.username))).scalars().all())


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db),
                      actor: User = Depends(rbac.require(rbac.P_USERS_MANAGE))):
    if body.role not in {r.value for r in Role}:
        raise HTTPException(status_code=422, detail="invalid role")
    if (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="username taken")
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role, full_name=body.full_name)
    db.add(u)
    await db.flush()
    await record_audit(db, AuditAction.USER_CREATED, actor=actor.username, entity_type="user", entity_id=u.id,
                       details={"username": u.username, "role": u.role})
    return u


@router.post("/users/{user_id}/deactivate", response_model=UserOut)
async def deactivate_user(user_id: str, db: AsyncSession = Depends(get_db),
                          actor: User = Depends(rbac.require(rbac.P_USERS_MANAGE))):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    if u.id == actor.id:
        raise HTTPException(status_code=400, detail="cannot deactivate yourself")
    u.is_active = False
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor.username, entity_type="user", entity_id=u.id,
                       details={"op": "deactivate"})
    return u
