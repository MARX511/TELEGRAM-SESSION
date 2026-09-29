"""Test fixtures. Uses PostgreSQL (tglegal_test) when reachable, otherwise a temporary SQLite database.
All filesystem roots point at a per-session temp directory so no real session/evidence data is touched."""
from __future__ import annotations

import asyncio
import os
import socket
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="tglegal_test_"))


def _pg_reachable() -> bool:
    try:
        with socket.create_connection(("localhost", 5432), timeout=1):
            return True
    except OSError:
        return False


PG_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://tglegal:tglegal@localhost:5432/tglegal_test")
DB_URL = PG_URL if _pg_reachable() else f"sqlite+aiosqlite:///{TMP / 'test.db'}"
os.environ.update({
    "DATABASE_URL": DB_URL, "APP_ENV": "local", "APP_SECRET_KEY": "test-secret-key-test-secret-key-0123456789",
    "SESSIONS_ROOT": str(TMP / "sessions"), "EVIDENCE_ROOT": str(TMP / "evidence"), "BACKUP_ROOT": str(TMP / "backups"),
    "EXPORT_ROOT": str(TMP / "exports"), "TELEGRAM_PROVIDER": "simulation", "QUARANTINE_FAILURE_THRESHOLD": "3",
    "CAPACITY_SESSION_CHECK_CONCURRENCY": "8", "CAPACITY_TASK_TIMEOUT_SECONDS": "10", "CAPACITY_MAX_RETRIES": "2",
    "CAPACITY_BACKOFF_BASE_SECONDS": "0.3", "CAPACITY_BACKOFF_MAX_SECONDS": "0.6", "WORKERS_ENABLED": "false",
    "SUBMISSION_EMAIL_ENABLED": "false",
})
for k in ("SESSION_FILE_ENCRYPTION_KEY", "REDIS_URL", "SMTP_HOST"):
    os.environ.pop(k, None)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.engine import get_engine, get_session_factory  # noqa: E402
from app.db.models import Base, User  # noqa: E402
from app.security.auth import hash_password  # noqa: E402
from app.services.reasons import seed_reasons  # noqa: E402
from app.services.templates import seed_templates  # noqa: E402
from app.telegram.session_files import create_synthetic_session_file  # noqa: E402

get_settings.cache_clear()
SETTINGS = get_settings()
SETTINGS.ensure_dirs()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables():
    """Truncate every table before each test and reset the sessions folder."""
    engine = get_engine()
    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
            await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        else:
            for t in reversed(Base.metadata.sorted_tables):
                await conn.execute(t.delete())
    import shutil

    for sub in ("active", "disabled", "quarantined"):
        d = SETTINGS.sessions_root / sub
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture
def settings():
    return SETTINGS


@pytest.fixture
def factory():
    return get_session_factory()


@pytest_asyncio.fixture
async def db(factory):
    async with factory() as session:
        yield session
        await session.commit()


@pytest_asyncio.fixture
async def seeded(db):
    await seed_reasons(db)
    await seed_templates(db)
    await db.commit()


@pytest.fixture
def make_session_file(settings):
    def _make(name: str, location: str = "active", fmt: str = "telethon", **kw) -> Path:
        return create_synthetic_session_file(settings.sessions_root / location / name, fmt=fmt, **kw)

    return _make


@pytest_asyncio.fixture
async def users(db):
    out = {}
    for role in ("admin", "operator", "reviewer", "auditor", "viewer"):
        u = User(username=role, password_hash=hash_password(f"{role}-pass-123"), role=role, full_name=role.title())
        db.add(u)
        out[role] = u
    await db.commit()
    return out


@pytest_asyncio.fixture
async def client(seeded, users):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def login(client: AsyncClient, role: str) -> dict:
    r = await client.post("/api/v1/auth/login", json={"username": role, "password": f"{role}-pass-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
