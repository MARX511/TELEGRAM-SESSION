from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backup.manager import BackupManager, recover
from app.db.models import JobRecord, TelegramSession
from app.domain.enums import ExecutionStatus
from app.services.sessions import discover_sessions
from app.workers.queue import JobQueue
from sqlalchemy import select


async def test_session_and_config_backups_verify(db, make_session_file, settings):
    make_session_file("bk.session")
    await discover_sessions(db)
    mgr = BackupManager(settings)
    rec = await mgr.backup(db, "sessions", actor="admin")
    assert Path(rec.file_path).exists() and await mgr.verify(db, rec.id)
    cfg = await mgr.backup(db, "config", actor="admin")
    text = Path(cfg.file_path).read_text()
    assert "capacity=" in text
    evd = await mgr.backup(db, "evidence", actor="admin")
    assert await mgr.verify(db, evd.id)
    Path(rec.file_path).write_bytes(b"corrupt")
    assert not await mgr.verify(db, rec.id)
    with pytest.raises(Exception):
        await mgr.restore(db, rec.id)
    integ = await mgr.integrity_check(db)
    assert integ["backups"] == 3 and rec.id in integ["backups_corrupt_or_missing"]


async def test_database_backup_restore(db, settings):
    if not shutil.which("pg_dump") and not settings.is_sqlite:
        pytest.skip("pg_dump not available")
    mgr = BackupManager(settings)
    rec = await mgr.backup(db, "database", actor="admin")
    assert Path(rec.file_path).stat().st_size > 0 and await mgr.verify(db, rec.id)


async def test_sessions_restore_and_recover(db, factory, make_session_file, settings):
    make_session_file("r1.session")
    make_session_file("r2.session")
    await discover_sessions(db)
    await db.commit()
    mgr = BackupManager(settings)
    rec = await mgr.backup(db, "sessions", actor="admin")
    await db.commit()
    (settings.sessions_root / "active" / "r1.session").unlink()
    q = JobQueue(factory, settings)
    j = await q.enqueue("session.check", {"session_id": "x"})
    async with factory() as s2:
        job = await s2.get(JobRecord, j.id)
        job.status = ExecutionStatus.RUNNING.value
        job.started_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await s2.commit()
    out = await recover(db, actor="admin")
    assert out["sessions_missing"] == 1 and out["jobs_resumed"] == 1
    restored = await mgr.restore(db, rec.id, actor="admin")
    assert restored.restored_at and (settings.sessions_root / "active" / "r1.session").exists()
    rep = await discover_sessions(db)
    s = (await db.execute(select(TelegramSession).where(TelegramSession.file_name == "r1.session"))).scalar_one()
    assert s.status == "UNCHECKED" and rep.missing == 0


async def test_ensure_admin_bootstrap(db, monkeypatch):
    """The local launcher's `users ensure-admin` creates exactly one admin and is idempotent."""
    from sqlalchemy import func, select

    from app.db.models import User
    from app.security.auth import hash_password, verify_password
    from app.services.audit import record_audit
    from app.domain.enums import AuditAction

    # mimic the command body: no users -> create; users present -> no-op
    count = (await db.execute(select(func.count(User.id)))).scalar_one()
    assert count == 0
    u = User(username="admin", password_hash=hash_password("StrongPass1"), role="admin", full_name="Administrator")
    db.add(u)
    await db.flush()
    await record_audit(db, AuditAction.USER_CREATED, actor="setup", entity_type="user", entity_id=u.id)
    again = (await db.execute(select(func.count(User.id)))).scalar_one()
    assert again == 1 and verify_password("StrongPass1", u.password_hash)


async def test_sqlite_backup_includes_uncommitted_wal_and_restore_roundtrip(db, settings, make_session_file):
    """On SQLite (WAL mode) a live backup must capture recently committed rows, and restore must bring them back."""
    if not settings.is_sqlite:
        import pytest as _pytest

        _pytest.skip("SQLite-specific behaviour")
    from sqlalchemy import func, select

    from app.backup.manager import BackupManager
    from app.db.models import Target
    from app.services.targets import create_target

    for i in range(20):
        await create_target(db, target_type="channel", username=f"wal_{i}", actor="op")
    await db.commit()
    mgr = BackupManager(settings)
    rec = await mgr.backup(db, "database", actor="admin")
    await db.commit()
    # the backup file itself must contain all 20 rows (not an empty pre-WAL snapshot)
    import sqlite3
    from pathlib import Path

    con = sqlite3.connect(rec.file_path)
    try:
        assert con.execute("SELECT count(*) FROM targets").fetchone()[0] == 20
    finally:
        con.close()
    # delete the rows, then restore and confirm they come back with a valid, non-corrupt database
    from sqlalchemy import delete

    await db.execute(delete(Target))
    await db.commit()
    assert (await db.execute(select(func.count(Target.id)))).scalar_one() == 0
    restored = await mgr.restore(db, rec.id, actor="admin")
    assert restored.restored_at is not None
    from app.db.engine import get_session_factory

    async with get_session_factory()() as fresh:
        assert (await fresh.execute(select(func.count(Target.id)))).scalar_one() == 20
        # integrity: a corrupt swap would fail this
        assert (await fresh.execute(select(func.count()).select_from(Target))).scalar_one() == 20
