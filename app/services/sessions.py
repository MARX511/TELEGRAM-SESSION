"""Session registry, validation, health and quarantine (§1, §2, §3, §29, §38).
Sessions are administered assets of the authorised operator. This service only discovers, validates and
classifies them; it never performs actions with them."""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import Account, Proxy, SessionCheck, SessionGroup, TelegramSession
from app.domain.enums import (VALID_SESSION_STATUSES, AuditAction, ErrorCategory, HealthState, SessionLocation,
                              SessionStatus)
from app.security.session_crypto import ENC_SUFFIX, SessionCrypto
from app.services.audit import record_audit
from app.services.errors import NotFoundError, RateLimited, ValidationFailed, record_error
from app.services.proxies import to_spec
from app.telegram.session_files import inspect_session_file
from app.telegram.validator import CheckResult, SessionValidator, get_validator
from app.utils import dumps, sha256_file

SESSION_GLOBS = ("*.session", "*.session" + ENC_SUFFIX)
TERMINAL_STATUSES = {SessionStatus.BANNED.value, SessionStatus.EXPIRED.value, SessionStatus.INVALID.value}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DiscoverReport:
    discovered: int = 0
    new: int = 0
    updated: int = 0
    missing: int = 0
    paths: list[str] = field(default_factory=list)


@dataclass
class BulkCheckReport:
    requested: int = 0
    checked: int = 0
    succeeded: int = 0
    failed: int = 0
    waiting: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _location_of(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return SessionLocation.ACTIVE.value
    first = rel.parts[0] if len(rel.parts) > 1 else ""
    return first if first in {loc.value for loc in SessionLocation} else SessionLocation.ACTIVE.value


# ----------------------------------------------------------------------------- discovery (§1, §2)
async def discover_sessions(db: AsyncSession, settings: Settings | None = None, actor: str | None = None) -> DiscoverReport:
    settings = settings or get_settings()
    settings.ensure_dirs()
    root = settings.sessions_root
    report = DiscoverReport()
    found: dict[str, Path] = {}
    for sub in SessionLocation:
        d = root / sub.value
        if d.exists():
            for pattern in SESSION_GLOBS:
                for p in sorted(d.glob(pattern)):
                    if p.is_file():
                        found[str(p.resolve())] = p
    for pattern in SESSION_GLOBS:  # legacy flat layout -> treated as active
        for p in sorted(root.glob(pattern)):
            if p.is_file():
                found[str(p.resolve())] = p
    report.discovered = len(found)

    existing = {s.file_path: s for s in (await db.execute(select(TelegramSession))).scalars().all()}
    for fp, p in found.items():
        info = inspect_session_file(p)
        enc = SessionCrypto.is_encrypted(p)
        digest = sha256_file(p)
        s = existing.pop(fp, None)
        if s is None:
            s = TelegramSession(file_path=fp, file_name=SessionCrypto.plain_name(p), file_sha256=digest,
                                file_size=info.size, file_format="encrypted" if enc else info.fmt, encrypted=enc,
                                location=_location_of(p, root), status=SessionStatus.UNCHECKED.value,
                                health=HealthState.UNAVAILABLE.value, telegram_id=info.user_id if not enc else None)
            if not enc and info.fmt in ("not_sqlite", "unknown"):
                s.status = SessionStatus.INVALID.value
                s.last_error = info.error
                s.last_error_category = ErrorCategory.VALIDATION_ERROR.value
                s.health = HealthState.CRITICAL.value
            db.add(s)
            await db.flush()
            report.new += 1
            await record_audit(db, AuditAction.SESSION_DISCOVERED, actor=actor, entity_type="session", entity_id=s.id,
                               session_id=s.id, details={"file": s.file_name, "format": s.file_format,
                                                         "location": s.location})
        else:
            changed = False
            if s.file_sha256 != digest:
                s.file_sha256, s.file_size, changed = digest, info.size, True
            loc = _location_of(p, root)
            if s.location != loc:
                s.location, changed = loc, True
            if s.encrypted != enc:
                s.encrypted, changed = enc, True
                s.file_format = "encrypted" if enc else info.fmt
            if s.status == SessionStatus.UNAVAILABLE.value and s.last_error == "file missing":
                s.status, s.last_error, changed = SessionStatus.UNCHECKED.value, None, True
            if changed:
                report.updated += 1
        report.paths.append(fp)

    for fp, s in existing.items():  # registry rows whose file disappeared
        if not (s.status == SessionStatus.UNAVAILABLE.value and s.last_error == "file missing"):
            s.status = SessionStatus.UNAVAILABLE.value
            s.health = HealthState.UNAVAILABLE.value
            s.last_error = "file missing"
            s.last_error_category = ErrorCategory.SESSION_ERROR.value
            report.missing += 1
            await record_audit(db, AuditAction.SESSION_CHECKED, actor=actor, entity_type="session", entity_id=s.id,
                               session_id=s.id, result=SessionStatus.UNAVAILABLE.value, reason="file missing")
    return report


# ----------------------------------------------------------------------------- health (§3)
def compute_health(s: TelegramSession, threshold: int) -> HealthState:
    if s.location == SessionLocation.QUARANTINED.value or not s.enabled:
        return HealthState.UNAVAILABLE
    if s.status in (SessionStatus.UNAVAILABLE.value, SessionStatus.UNCHECKED.value):
        return HealthState.UNAVAILABLE
    if s.status in TERMINAL_STATUSES or s.consecutive_failures >= threshold:
        return HealthState.CRITICAL
    if s.rate_limited_until and s.rate_limited_until > _now():
        return HealthState.WARNING
    if s.consecutive_failures > 0:
        return HealthState.WARNING
    if s.check_count >= 5 and s.availability < 0.8:
        return HealthState.WARNING
    if s.status in {v.value for v in VALID_SESSION_STATUSES}:
        return HealthState.HEALTHY
    return HealthState.WARNING


def health_snapshot(s: TelegramSession) -> dict:
    return {
        "session_id": s.id, "file_name": s.file_name, "status": s.status, "health": s.health,
        "last_check": s.last_check, "last_successful_operation": s.last_success_at, "last_failure": s.last_failure_at,
        "failure_count": s.failure_count, "consecutive_failures": s.consecutive_failures,
        "availability": round(s.availability, 3), "rate_limited_until": s.rate_limited_until,
        "proxy_id": s.proxy_id, "location": s.location, "enabled": s.enabled,
    }


# ----------------------------------------------------------------------------- checks (§2, §7)
async def _resolve_proxy(db: AsyncSession, s: TelegramSession) -> Proxy | None:
    pid = s.proxy_id
    if not pid and s.account_id:
        acc = await db.get(Account, s.account_id)
        pid = acc.proxy_id if acc else None
    if not pid and s.group_id:
        grp = await db.get(SessionGroup, s.group_id)
        pid = grp.proxy_id if grp else None
    return await db.get(Proxy, pid) if pid else None


async def _link_account(db: AsyncSession, s: TelegramSession, r: CheckResult) -> None:
    if not r.telegram_id:
        return
    acc = (await db.execute(select(Account).where(Account.telegram_id == r.telegram_id))).scalar_one_or_none()
    if acc is None:
        acc = Account(telegram_id=r.telegram_id, username=r.username, phone_masked=r.phone_masked,
                      display_name=r.display_name)
        db.add(acc)
        await db.flush()
    else:
        acc.username = r.username or acc.username
        acc.display_name = r.display_name or acc.display_name
        acc.phone_masked = r.phone_masked or acc.phone_masked
    s.account_id = acc.id


async def check_session(db: AsyncSession, s: TelegramSession, *, validator: SessionValidator | None = None,
                        settings: Settings | None = None, actor: str | None = None, crypto: SessionCrypto | None = None,
                        force: bool = False) -> SessionCheck:
    settings = settings or get_settings()
    validator = validator or get_validator(settings.telegram_provider)
    now = _now()
    if not s.enabled and not force:
        raise ValidationFailed("session is disabled; enable it before checking")
    if s.rate_limited_until and s.rate_limited_until > now and not force:
        wait = int((s.rate_limited_until - now).total_seconds()) + 1
        raise RateLimited(f"server asked to wait; {wait}s remaining for this session", wait)

    path = Path(s.file_path)
    check = SessionCheck(session_id=s.id, provider=validator.provider, operator=actor, started_at=now,
                         result_status=SessionStatus.CHECK_FAILED.value, success=False)
    tmp: Path | None = None
    result: CheckResult
    try:
        if not path.exists():
            result = CheckResult(status=SessionStatus.UNAVAILABLE, success=False,
                                 error_category=ErrorCategory.SESSION_ERROR, error_message="file missing")
        else:
            target: Path | None = path
            if s.encrypted or SessionCrypto.is_encrypted(path):
                crypto = crypto or SessionCrypto(settings.session_file_encryption_key)
                if not crypto.enabled:
                    target = None
                    result = CheckResult(status=SessionStatus.CHECK_FAILED, success=False,
                                         error_category=ErrorCategory.VALIDATION_ERROR,
                                         error_message="session file is encrypted but no SESSION_FILE_ENCRYPTION_KEY is set")
                else:
                    try:
                        tmp = crypto.decrypt_to_temp(path)
                        target = tmp
                    except ValueError as exc:
                        target = None
                        result = CheckResult(status=SessionStatus.CHECK_FAILED, success=False,
                                             error_category=ErrorCategory.VALIDATION_ERROR, error_message=str(exc))
            if target is not None:
                spec = to_spec(await _resolve_proxy(db, s))
                try:
                    result = await asyncio.wait_for(validator.check(target, spec),
                                                    timeout=settings.capacity.task_timeout_seconds)
                except asyncio.TimeoutError:
                    result = CheckResult(status=SessionStatus.CHECK_FAILED, success=False,
                                         error_category=ErrorCategory.NETWORK_ERROR,
                                         error_message=f"check timed out after {settings.capacity.task_timeout_seconds}s")
    finally:
        if tmp is not None:
            for extra in (tmp, tmp.with_name(tmp.name + "-journal")):
                try:
                    extra.unlink()
                except FileNotFoundError:
                    pass
    return await _apply_result(db, s, check, result, settings, actor)


async def _apply_result(db: AsyncSession, s: TelegramSession, check: SessionCheck, r: CheckResult,
                        settings: Settings, actor: str | None) -> SessionCheck:
    now = _now()
    check.finished_at = now
    check.result_status = r.status.value
    check.success = r.success
    check.latency_ms = r.latency_ms
    check.error_category = r.error_category.value if r.error_category else None
    check.error_message = r.error_message
    check.server_wait_seconds = r.server_wait_seconds
    check.details_json = dumps(r.details) if r.details else None
    db.add(check)

    s.last_check = now
    s.check_count += 1
    if r.success:
        s.status = r.status.value
        s.last_success_at = now
        s.consecutive_failures = 0
        s.last_error = None
        s.last_error_category = None
        s.rate_limited_until = None
        s.telegram_id = r.telegram_id or s.telegram_id
        s.username = r.username or s.username
        s.phone_masked = r.phone_masked or s.phone_masked
        await _link_account(db, s, r)
    else:
        s.last_failure_at = now
        s.failure_count += 1
        s.last_error = r.error_message
        s.last_error_category = r.error_category.value if r.error_category else None
        if r.error_category == ErrorCategory.RATE_LIMIT:
            # §7: pause this session for the server-defined delay; not a validity failure, never bypassed.
            wait = r.server_wait_seconds or int(settings.capacity.backoff_max_seconds)
            s.rate_limited_until = now + timedelta(seconds=wait)
            if s.status == SessionStatus.UNCHECKED.value:
                s.status = SessionStatus.CHECK_FAILED.value
        else:
            s.consecutive_failures += 1
            s.status = r.status.value
        await record_error(db, r.error_category or ErrorCategory.UNKNOWN_ERROR, r.error_message or "check failed",
                           component="sessions", session_id=s.id, operator=actor, provider=check.provider,
                           details={"status": s.status, "server_wait_seconds": r.server_wait_seconds})

    successes = s.check_count - s.failure_count
    s.availability = max(0.0, successes / s.check_count) if s.check_count else 0.0
    s.health = compute_health(s, settings.quarantine_failure_threshold).value
    await record_audit(db, AuditAction.SESSION_CHECKED, actor=actor, entity_type="session", entity_id=s.id,
                       session_id=s.id, provider=check.provider, result=s.status, reason=r.error_message,
                       details={"success": r.success, "latency_ms": r.latency_ms, "health": s.health})

    # §38: VALID -> CHECK -> FAILED -> QUARANTINE -> MARK UNAVAILABLE -> operator action required
    if s.location == SessionLocation.ACTIVE.value and (
        s.status in TERMINAL_STATUSES or s.consecutive_failures >= settings.quarantine_failure_threshold
    ):
        await quarantine_session(db, s, actor=actor, settings=settings,
                                 reason=f"{s.status}: {s.last_error or 'repeated failures'}")
    return check


async def check_many(session_factory: async_sessionmaker[AsyncSession], session_ids: list[str], *,
                     settings: Settings | None = None, validator: SessionValidator | None = None,
                     actor: str | None = None, concurrency: int | None = None) -> BulkCheckReport:
    """Bulk health check with a configurable concurrency limit (§6). Each session is its own DB transaction."""
    settings = settings or get_settings()
    validator = validator or get_validator(settings.telegram_provider)
    sem = asyncio.Semaphore(concurrency or settings.capacity.session_check_concurrency)
    report = BulkCheckReport(requested=len(session_ids))
    lock = asyncio.Lock()

    async def one(sid: str) -> None:
        async with sem:
            async with session_factory() as db:
                try:
                    s = await db.get(TelegramSession, sid)
                    if s is None:
                        async with lock:
                            report.skipped += 1
                        return
                    try:
                        chk = await check_session(db, s, validator=validator, settings=settings, actor=actor)
                    except RateLimited:
                        async with lock:
                            report.waiting += 1
                        await db.commit()
                        return
                    except ValidationFailed as exc:
                        async with lock:
                            report.skipped += 1
                            report.errors.append(f"{sid}: {exc.message}")
                        await db.commit()
                        return
                    await db.commit()
                    async with lock:
                        report.checked += 1
                        if chk.success:
                            report.succeeded += 1
                        elif chk.error_category == ErrorCategory.RATE_LIMIT.value:
                            report.waiting += 1
                        else:
                            report.failed += 1
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    async with lock:
                        report.errors.append(f"{sid}: {type(exc).__name__}: {exc}")

    await asyncio.gather(*(one(sid) for sid in session_ids))
    return report


# ----------------------------------------------------------------------------- storage moves (§29, §38)
def _sidecars(p: Path) -> list[Path]:
    return [p, p.with_name(p.name + "-journal")]


async def move_session(db: AsyncSession, s: TelegramSession, to: SessionLocation, *, actor: str | None = None,
                       reason: str | None = None, settings: Settings | None = None) -> TelegramSession:
    settings = settings or get_settings()
    settings.ensure_dirs()
    src = Path(s.file_path)
    dest_dir = settings.sessions_root / to.value
    dest = dest_dir / src.name
    if src.exists() and src.resolve() != dest.resolve():
        if dest.exists():
            dest = dest_dir / f"{src.stem}_{s.id[:8]}{src.suffix}"
        for extra in _sidecars(src):
            if extra.exists():
                shutil.move(str(extra), str(dest_dir / (dest.name + extra.name[len(src.name):])))
    s.file_path = str(dest.resolve())
    s.location = to.value
    await record_audit(db, AuditAction.SESSION_MOVED, actor=actor, entity_type="session", entity_id=s.id,
                       session_id=s.id, result=to.value, reason=reason)
    return s


async def quarantine_session(db: AsyncSession, s: TelegramSession, *, actor: str | None = None,
                             reason: str | None = None, settings: Settings | None = None) -> TelegramSession:
    await move_session(db, s, SessionLocation.QUARANTINED, actor=actor, reason=reason, settings=settings)
    s.enabled = False
    s.health = HealthState.UNAVAILABLE.value
    await record_audit(db, AuditAction.SESSION_QUARANTINED, actor=actor, entity_type="session", entity_id=s.id,
                       session_id=s.id, result=s.status, reason=reason,
                       details={"operator_action_required": "re-authorise or retire this session"})
    return s


async def disable_session(db: AsyncSession, s: TelegramSession, *, actor: str | None = None, reason: str | None = None,
                          settings: Settings | None = None) -> TelegramSession:
    await move_session(db, s, SessionLocation.DISABLED, actor=actor, reason=reason, settings=settings)
    s.enabled = False
    s.health = HealthState.UNAVAILABLE.value
    return s


async def enable_session(db: AsyncSession, s: TelegramSession, *, actor: str | None = None, reason: str | None = None,
                         settings: Settings | None = None) -> TelegramSession:
    await move_session(db, s, SessionLocation.ACTIVE, actor=actor, reason=reason, settings=settings)
    s.enabled = True
    s.consecutive_failures = 0
    s.status = SessionStatus.UNCHECKED.value  # §2: never valid until a check succeeds
    s.health = HealthState.UNAVAILABLE.value
    return s


async def encrypt_session_file(db: AsyncSession, s: TelegramSession, crypto: SessionCrypto, *,
                               actor: str | None = None) -> TelegramSession:
    p = Path(s.file_path)
    if s.encrypted or SessionCrypto.is_encrypted(p):
        return s
    if not p.exists():
        raise NotFoundError("session file missing")
    out = crypto.encrypt_file(p, remove_plain=True)
    s.file_path = str(out.resolve())
    s.encrypted = True
    s.file_format = "encrypted"
    s.file_sha256 = sha256_file(out)
    s.file_size = out.stat().st_size
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="session", entity_id=s.id,
                       session_id=s.id, details={"op": "encrypt_at_rest"})
    return s


# ----------------------------------------------------------------------------- queries (§23)
async def get_session(db: AsyncSession, session_id: str) -> TelegramSession:
    s = await db.get(TelegramSession, session_id)
    if not s:
        raise NotFoundError("session not found")
    return s


async def list_sessions(db: AsyncSession, *, status: str | None = None, health: str | None = None,
                        location: str | None = None, group_id: str | None = None, search: str | None = None,
                        sort: str = "file_name", order: str = "asc", limit: int = 100, offset: int = 0
                        ) -> tuple[list[TelegramSession], int]:
    q = select(TelegramSession)
    if status:
        q = q.where(TelegramSession.status == status)
    if health:
        q = q.where(TelegramSession.health == health)
    if location:
        q = q.where(TelegramSession.location == location)
    if group_id:
        q = q.where(TelegramSession.group_id == group_id)
    if search:
        like = f"%{search}%"
        q = q.where(or_(TelegramSession.file_name.ilike(like), TelegramSession.username.ilike(like),
                        TelegramSession.tags.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    col = getattr(TelegramSession, sort, TelegramSession.file_name)
    q = q.order_by(col.desc() if order == "desc" else col.asc()).limit(limit).offset(offset)
    return list((await db.execute(q)).scalars().all()), total


async def session_stats(db: AsyncSession) -> dict:
    total = (await db.execute(select(func.count(TelegramSession.id)))).scalar_one()
    by_status = {r[0]: r[1] for r in (await db.execute(
        select(TelegramSession.status, func.count()).group_by(TelegramSession.status))).all()}
    by_health = {r[0]: r[1] for r in (await db.execute(
        select(TelegramSession.health, func.count()).group_by(TelegramSession.health))).all()}
    by_location = {r[0]: r[1] for r in (await db.execute(
        select(TelegramSession.location, func.count()).group_by(TelegramSession.location))).all()}
    return {
        "total": total,
        "active": by_status.get(SessionStatus.ACTIVE.value, 0),
        "valid": by_status.get(SessionStatus.VALID.value, 0),
        "banned": by_status.get(SessionStatus.BANNED.value, 0),
        "invalid": by_status.get(SessionStatus.INVALID.value, 0),
        "expired": by_status.get(SessionStatus.EXPIRED.value, 0),
        "unavailable": by_status.get(SessionStatus.UNAVAILABLE.value, 0),
        "check_failed": by_status.get(SessionStatus.CHECK_FAILED.value, 0),
        "unchecked": by_status.get(SessionStatus.UNCHECKED.value, 0),
        "by_status": by_status, "by_health": by_health, "by_location": by_location,
    }
