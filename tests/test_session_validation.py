from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Account, SessionCheck, TelegramSession
from app.domain.enums import ErrorCategory, HealthState, SessionLocation, SessionStatus
from app.security.session_crypto import SessionCrypto, generate_key
from app.services.errors import RateLimited
from app.services.sessions import (check_many, check_session, discover_sessions, enable_session, encrypt_session_file,
                                   quarantine_session)


async def _one(db, make_session_file, name):
    make_session_file(name)
    await discover_sessions(db)
    return (await db.execute(select(TelegramSession).where(TelegramSession.file_name == name))).scalar_one()


async def test_valid_session_links_account_and_is_healthy(db, make_session_file):
    s = await _one(db, make_session_file, "good.session")
    chk = await check_session(db, s, actor="tester")
    assert chk.success and chk.result_status == SessionStatus.VALID.value and chk.provider == "simulation"
    assert s.status == SessionStatus.VALID.value and s.health == HealthState.HEALTHY.value
    assert s.telegram_id and s.username and s.account_id
    acc = await db.get(Account, s.account_id)
    assert acc.telegram_id == s.telegram_id
    assert s.availability == 1.0 and s.consecutive_failures == 0


async def test_banned_session_is_quarantined_immediately(db, make_session_file, settings):
    s = await _one(db, make_session_file, "acc_banned.session")
    await check_session(db, s)
    assert s.status == SessionStatus.BANNED.value
    assert s.location == SessionLocation.QUARANTINED.value and not s.enabled
    assert s.health == HealthState.UNAVAILABLE.value  # §38: quarantined => unavailable, operator action required
    assert Path(s.file_path).exists() and Path(s.file_path).parent.name == "quarantined"
    assert not (settings.sessions_root / "active" / "acc_banned.session").exists()
    assert s.last_error_category == ErrorCategory.AUTH_ERROR.value


async def test_network_failures_quarantine_after_threshold(db, make_session_file, settings):
    s = await _one(db, make_session_file, "acc_netfail.session")
    for i in range(settings.quarantine_failure_threshold - 1):
        await check_session(db, s)
        assert s.location == SessionLocation.ACTIVE.value
        assert s.health == HealthState.WARNING.value
        assert s.status == SessionStatus.CHECK_FAILED.value
    await check_session(db, s)
    assert s.consecutive_failures == settings.quarantine_failure_threshold
    assert s.location == SessionLocation.QUARANTINED.value
    assert s.failure_count == settings.quarantine_failure_threshold


async def test_flood_wait_pauses_session_without_bypass(db, make_session_file):
    s = await _one(db, make_session_file, "acc_flood.session")
    chk = await check_session(db, s)
    assert chk.error_category == ErrorCategory.RATE_LIMIT.value and chk.server_wait_seconds == 30
    assert s.rate_limited_until and s.rate_limited_until > datetime.now(timezone.utc) + timedelta(seconds=20)
    assert s.consecutive_failures == 0            # not a validity failure
    assert s.location == SessionLocation.ACTIVE.value  # not quarantined
    assert s.health == HealthState.WARNING.value
    with pytest.raises(RateLimited) as exc:
        await check_session(db, s)               # retry before the server delay elapsed is refused
    assert exc.value.wait_seconds > 0
    # once the window has passed the check runs again
    s.rate_limited_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    chk2 = await check_session(db, s)
    assert chk2.error_category == ErrorCategory.RATE_LIMIT.value


async def test_disabled_session_refuses_check_and_enable_requires_recheck(db, make_session_file):
    s = await _one(db, make_session_file, "good2.session")
    await check_session(db, s)
    await quarantine_session(db, s, actor="op", reason="manual")
    from app.services.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        await check_session(db, s)
    await enable_session(db, s, actor="op")
    assert s.status == SessionStatus.UNCHECKED.value and s.location == SessionLocation.ACTIVE.value and s.enabled
    await check_session(db, s)
    assert s.status == SessionStatus.VALID.value


async def test_encrypted_session_file_roundtrip(db, make_session_file, settings, monkeypatch):
    s = await _one(db, make_session_file, "secret.session")
    key = generate_key()
    crypto = SessionCrypto(key)
    await encrypt_session_file(db, s, crypto, actor="op")
    assert s.encrypted and s.file_path.endswith(".session.enc") and Path(s.file_path).exists()
    assert not (settings.sessions_root / "active" / "secret.session").exists()
    raw = Path(s.file_path).read_bytes()
    assert not raw.startswith(b"SQLite format 3")
    # without a key the check fails safely
    chk = await check_session(db, s, crypto=SessionCrypto(None))
    assert not chk.success and chk.error_category == ErrorCategory.VALIDATION_ERROR.value
    # with the key it validates and leaves no plaintext behind
    chk2 = await check_session(db, s, crypto=crypto, force=True)
    assert chk2.success
    assert not list(Path("/tmp").glob("tgsess_*.session"))
    # discovery keeps the encrypted flag
    rep = await discover_sessions(db)
    assert rep.new == 0


async def test_bulk_check_with_concurrency(db, factory, make_session_file, settings):
    names = [f"bulk_{i:03d}.session" for i in range(12)] + ["bulk_banned.session", "bulk_flood.session", "bulk_expired.session"]
    for n in names:
        make_session_file(n)
    await discover_sessions(db)
    await db.commit()
    ids = list((await db.execute(select(TelegramSession.id))).scalars().all())
    rep = await check_many(factory, ids, concurrency=4, actor="bulk")
    assert rep.requested == 15 and rep.checked == 15
    assert rep.succeeded == 12 and rep.waiting == 1 and rep.failed == 2 and not rep.errors
    checks = (await db.execute(select(SessionCheck))).scalars().all()
    assert len(checks) == 15
    db.expire_all()
    rows = (await db.execute(select(TelegramSession))).scalars().all()
    assert sum(1 for r in rows if r.location == "quarantined") == 2
