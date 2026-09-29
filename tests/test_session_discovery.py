from __future__ import annotations

from app.db.models import TelegramSession
from app.domain.enums import SessionStatus
from app.services.sessions import discover_sessions, session_stats
from app.telegram.session_files import inspect_session_file
from sqlalchemy import select


async def test_inspect_formats(make_session_file, settings):
    t = make_session_file("acc_t.session", fmt="telethon")
    p = make_session_file("acc_p.session", fmt="pyrogram", user_id=4242)
    assert inspect_session_file(t).fmt == "telethon"
    info = inspect_session_file(p)
    assert info.fmt == "pyrogram" and info.user_id == 4242
    junk = settings.sessions_root / "active" / "junk.session"
    junk.write_bytes(b"not a database at all, definitely not sqlite header")
    assert inspect_session_file(junk).fmt == "not_sqlite"


async def test_discover_indexes_and_classifies(db, make_session_file, settings):
    for i in range(5):
        make_session_file(f"account_{i:03d}.session")
    make_session_file("disabled_one.session", location="disabled")
    (settings.sessions_root / "active" / "broken.session").write_bytes(b"garbage" * 10)
    rep = await discover_sessions(db, actor="test")
    assert rep.discovered == 7 and rep.new == 7
    rows = (await db.execute(select(TelegramSession))).scalars().all()
    by_name = {r.file_name: r for r in rows}
    assert by_name["account_000.session"].status == SessionStatus.UNCHECKED.value  # never valid before a check (§2)
    assert by_name["disabled_one.session"].location == "disabled"
    assert by_name["broken.session"].status == SessionStatus.INVALID.value
    assert by_name["broken.session"].last_error
    # idempotent
    rep2 = await discover_sessions(db, actor="test")
    assert rep2.new == 0 and rep2.discovered == 7


async def test_discover_marks_missing_files(db, make_session_file, settings):
    p = make_session_file("gone.session")
    await discover_sessions(db)
    p.unlink()
    rep = await discover_sessions(db)
    assert rep.missing == 1
    s = (await db.execute(select(TelegramSession).where(TelegramSession.file_name == "gone.session"))).scalar_one()
    assert s.status == SessionStatus.UNAVAILABLE.value and s.last_error == "file missing"
    stats = await session_stats(db)
    assert stats["unavailable"] == 1 and stats["total"] == 1
