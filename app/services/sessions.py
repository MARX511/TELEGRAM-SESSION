"""Session registry, validation, health and quarantine (§1, §2, §3, §29, §38).
Sessions are administered assets of the authorised operator. This service only discovers, validates and
classifies them; it never performs actions with them."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shutil
import uuid
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

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


# ----------------------------------------------------------------------------- upload / import (§1)
# Files uploaded from the dashboard or the API are staged in a private folder, validated as real Telethon /
# Pyrogram session databases, de-duplicated by content, moved into sessions/active under a sanitised name, then
# registered through the normal discovery path (and encrypted at rest when a key is configured).
MAX_SESSION_FILE_BYTES = 64 * 1024 * 1024   # a session database is normally well under 1 MB
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024   # across all members of one archive (zip-bomb guard)
MAX_ARCHIVE_MEMBERS = 5000
MAX_DEDUPE_DECRYPT = 5000                   # encrypted registry files decrypted to compare content
_CHUNK = 1 << 20
_UNSAFE_NAME = re.compile(r"[^\w.\-]+")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

REJECT_TYPE = "only .session files or a .zip of them are accepted"
REJECT_TOO_LARGE = "file is too large"
REJECT_NOT_SESSION = "not a Telethon or Pyrogram session file"
REJECT_BAD_ZIP = "not a readable ZIP archive"
REJECT_EMPTY_ZIP = "the ZIP contains no .session files"
REJECT_REASONS = (REJECT_TYPE, REJECT_TOO_LARGE, REJECT_NOT_SESSION, REJECT_BAD_ZIP, REJECT_EMPTY_ZIP)


class UploadLike(Protocol):
    filename: str | None

    async def read(self, size: int = -1) -> bytes: ...


@dataclass
class ImportReport:
    added: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    session_ids: list[str] = field(default_factory=list)
    encrypted: int = 0

    def as_dict(self) -> dict:
        return {"added": self.added, "duplicates": self.duplicates, "encrypted": self.encrypted,
                "rejected": [{"file": f, "reason": r} for f, r in self.rejected], "session_ids": self.session_ids}


def safe_session_name(name: str) -> str:
    """Basename only (no directories, no traversal), word characters, dots and dashes, always '.session'."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if base.lower().endswith(".session"):
        base = base[: -len(".session")]
    stem = _UNSAFE_NAME.sub("_", base).strip("._-") or "session"
    if stem.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        stem = "_" + stem
    return stem[:100] + ".session"


def _free_destination(folder: Path, name: str) -> Path:
    stem = name[: -len(".session")]
    for i in range(1, 100_000):
        candidate = folder / (name if i == 1 else f"{stem}_{i}.session")
        if not candidate.exists() and not candidate.with_name(candidate.name + ENC_SUFFIX).exists():
            return candidate
    raise ValidationFailed("no free file name in sessions/active")


async def _spool(upload: UploadLike, dest: Path, limit: int) -> bool:
    size = 0
    with open(dest, "wb") as f:
        while chunk := await upload.read(_CHUNK):
            size += len(chunk)
            if size > limit:
                return False
            f.write(chunk)
    return True


def _expand_zip(archive: Path, staging: Path, label: str, report: ImportReport) -> list[tuple[str, str, Path]]:
    """Extract only *.session members, by basename, with a hard byte cap per member (not trusting the header)."""
    out: list[tuple[str, str, Path]] = []
    rejected_before = len(report.rejected)
    extracted = 0
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError):
        report.rejected.append((label, REJECT_BAD_ZIP))
        return out
    with zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        if len(members) > MAX_ARCHIVE_MEMBERS:
            report.rejected.append((label, REJECT_TOO_LARGE))
            return out
        for info in members:
            inner = info.filename.replace("\\", "/")
            base = inner.rsplit("/", 1)[-1]
            if "__MACOSX/" in inner or base.startswith(".") or not base.lower().endswith(".session"):
                continue
            shown = f"{label}/{base}"
            if info.file_size > MAX_SESSION_FILE_BYTES or extracted + info.file_size > MAX_EXTRACTED_BYTES:
                report.rejected.append((shown, REJECT_TOO_LARGE))
                continue
            dest = staging / f"{uuid.uuid4().hex}.session"
            size, ok = 0, True
            try:
                with zf.open(info) as src, open(dest, "wb") as f:
                    while chunk := src.read(_CHUNK):
                        size += len(chunk)
                        if size > MAX_SESSION_FILE_BYTES or extracted + size > MAX_EXTRACTED_BYTES:
                            ok = False  # the header lied about the size: stop reading
                            break
                        f.write(chunk)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error):
                report.rejected.append((shown, REJECT_BAD_ZIP))  # corrupt, password-protected or unsupported method
                continue
            if not ok:
                report.rejected.append((shown, REJECT_TOO_LARGE))
                continue
            extracted += size
            out.append((shown, base, dest))
    if not out and len(report.rejected) == rejected_before:
        report.rejected.append((label, REJECT_EMPTY_ZIP))
    return out


async def _known_plain_hashes(db: AsyncSession, crypto: SessionCrypto) -> set[str]:
    rows = (await db.execute(select(TelegramSession.file_path, TelegramSession.file_sha256,
                                    TelegramSession.encrypted))).all()
    known = {sha for _, sha, enc in rows if sha and not enc}
    encrypted = [Path(fp) for fp, _, enc in rows if enc]
    if crypto.enabled and len(encrypted) <= MAX_DEDUPE_DECRYPT:
        def _hash_all() -> set[str]:
            out = set()
            for p in encrypted:
                try:
                    out.add(hashlib.sha256(crypto.decrypt_bytes(p)).hexdigest())
                except (OSError, ValueError):
                    continue
            return out
        known |= await asyncio.to_thread(_hash_all)
    return known


async def import_uploads(db: AsyncSession, uploads: Iterable[UploadLike], *, actor: str | None = None,
                         settings: Settings | None = None) -> ImportReport:
    settings = settings or get_settings()
    settings.ensure_dirs()
    report = ImportReport()
    active = settings.sessions_root / SessionLocation.ACTIVE.value
    active.mkdir(parents=True, exist_ok=True)
    staging = settings.sessions_root / ".incoming" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    crypto = SessionCrypto(settings.session_file_encryption_key)
    placed: list[Path] = []
    try:
        candidates: list[tuple[str, str, Path]] = []  # (name shown to the user, original file name, staged path)
        for up in uploads:
            label = (getattr(up, "filename", None) or "").replace("\\", "/").rsplit("/", 1)[-1].strip() or "file"
            low = label.lower()
            if low.endswith(".zip"):
                staged = staging / f"{uuid.uuid4().hex}.zip"
                if not await _spool(up, staged, MAX_ARCHIVE_BYTES):
                    report.rejected.append((label, REJECT_TOO_LARGE))
                    continue
                candidates += await asyncio.to_thread(_expand_zip, staged, staging, label, report)
            elif low.endswith(".session"):
                staged = staging / f"{uuid.uuid4().hex}.session"
                if not await _spool(up, staged, MAX_SESSION_FILE_BYTES):
                    report.rejected.append((label, REJECT_TOO_LARGE))
                    continue
                candidates.append((label, label, staged))
            else:
                report.rejected.append((label, REJECT_TYPE))
        known = await _known_plain_hashes(db, crypto) if candidates else set()
        for shown, original, staged in candidates:
            info = inspect_session_file(staged)
            if info.fmt not in ("telethon", "pyrogram"):
                report.rejected.append((shown, REJECT_NOT_SESSION))
                continue
            digest = sha256_file(staged)
            if digest in known:
                report.duplicates.append(shown)
                continue
            dest = _free_destination(active, safe_session_name(original))
            shutil.move(str(staged), str(dest))
            with contextlib.suppress(OSError):
                os.chmod(dest, 0o600)
            known.add(digest)
            placed.append(dest)
            report.added.append(dest.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if placed:
        await discover_sessions(db, settings, actor=actor)
        wanted = [str(p.resolve()) for p in placed]
        rows = (await db.execute(select(TelegramSession).where(TelegramSession.file_path.in_(wanted)))).scalars().all()
        for s in rows:
            if crypto.enabled:
                await encrypt_session_file(db, s, crypto, actor=actor)
                report.encrypted += 1
            report.session_ids.append(s.id)
    await record_audit(db, AuditAction.SESSION_UPLOADED, actor=actor, entity_type="session",
                       result=f"{len(report.added)} added", details={
                           "added": report.added, "duplicates": report.duplicates, "encrypted": report.encrypted,
                           "rejected": [{"file": f, "reason": r} for f, r in report.rejected]})
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
