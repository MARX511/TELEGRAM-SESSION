"""The UTCDateTime column type must hand back timezone-aware UTC datetimes on every backend, so comparisons
with datetime.now(timezone.utc) never raise on SQLite (which stores naive datetimes)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import TelegramSession
from app.services.sessions import check_session, discover_sessions
from app.workers.queue import JobQueue


async def test_timestamps_come_back_aware(db, make_session_file):
    make_session_file("tz.session")
    await discover_sessions(db)
    s = (await db.execute(select(TelegramSession))).scalar_one()
    await check_session(db, s, actor="t")
    await db.flush()
    db.expire_all()
    s = (await db.execute(select(TelegramSession))).scalar_one()
    assert s.created_at.tzinfo is not None and s.last_check.tzinfo is not None
    # the comparison that used to raise TypeError on SQLite
    assert s.created_at <= datetime.now(timezone.utc)
    delta = datetime.now(timezone.utc) - s.last_check
    assert timedelta(0) <= delta < timedelta(minutes=5)


async def test_job_not_before_is_comparable(db, factory, settings):
    q = JobQueue(factory, settings)
    j = await q.enqueue("noop", {}, not_before=datetime.now(timezone.utc) + timedelta(hours=1))
    fresh = await q.get(j.id)
    assert fresh.not_before.tzinfo is not None
    assert fresh.not_before > datetime.now(timezone.utc)
