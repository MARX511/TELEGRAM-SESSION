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
