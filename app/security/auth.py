from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import get_db
from app.db.models import User

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)
COOKIE_NAME = "tg_access"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        return False


def create_access_token(user: User, expires_minutes: int | None = None) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes or s.access_token_expire_minutes)
    payload = {"sub": user.id, "username": user.username, "role": user.role, "exp": exp}
    return jwt.encode(payload, s.app_secret_key, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    s = get_settings()
    try:
        return jwt.decode(token, s.app_secret_key, algorithms=[s.jwt_algorithm])
    except JWTError:
        return None


async def authenticate(db: AsyncSession, username: str, password: str) -> User | None:
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    return user


async def get_current_user_optional(request: Request, token: str | None = Depends(oauth2_scheme),
                                    db: AsyncSession = Depends(get_db)) -> User | None:
    raw = token or request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    payload = decode_token(raw)
    if not payload:
        return None
    user = await db.get(User, payload.get("sub"))
    if not user or not user.is_active:
        return None
    return user


async def get_current_user(user: User | None = Depends(get_current_user_optional)) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


async def ensure_bootstrap_admin(db: AsyncSession, username: str, password: str) -> User | None:
    """Create the first admin if no users exist. Returns the user when created."""
    existing = (await db.execute(select(User).limit(1))).scalar_one_or_none()
    if existing:
        return None
    user = User(username=username, password_hash=hash_password(password), role="admin", full_name="Administrator")
    db.add(user)
    await db.flush()
    return user
