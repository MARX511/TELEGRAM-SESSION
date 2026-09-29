"""Backup / restore / recovery (§20, §30, §31). Each backup type is separate and hashed; restores are audited."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import BackupRecord, TelegramSession
from app.domain.enums import AuditAction
from app.services.audit import record_audit
from app.services.errors import NotFoundError, ValidationFailed
from app.utils import sha256_file

BACKUP_TYPES = ("database", "sessions", "config", "evidence")
REDACT_KEYS = ("SECRET", "PASSWORD", "KEY", "HASH", "TOKEN")


class BackupManager:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()

    def _stamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # ------------------------------------------------------------------ database
    def _pg_parts(self) -> dict:
        u = urlparse(self.settings.database_url.replace("+asyncpg", ""))
        return {"host": u.hostname or "localhost", "port": str(u.port or 5432), "user": u.username or "",
                "password": u.password or "", "dbname": u.path.lstrip("/")}

    def _sqlite_path(self) -> Path:
        # sqlite+aiosqlite:///./data/x.db -> ./data/x.db ; ...////abs/x.db -> /abs/x.db ; ...///C:/x.db -> C:/x.db
        return Path(self.settings.database_url.split("///", 1)[1])

    @staticmethod
    def _sqlite_online_copy(src: Path, dst: Path) -> None:
        """Copy a SQLite database through SQLite's own online backup API. Reads through the pager so committed
        rows sitting in the WAL are included (a plain file copy would miss them), and produces a complete,
        standalone destination database with no separate -wal to reapply."""
        import sqlite3

        source = sqlite3.connect(str(src), timeout=30)
        try:
            dest = sqlite3.connect(str(dst))
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()

    async def _backup_database(self) -> Path:
        out = self.settings.backup_root / f"db_{self._stamp()}"
        if self.settings.is_sqlite:
            out = out.with_suffix(".sqlite")
            await asyncio.to_thread(self._sqlite_online_copy, self._sqlite_path(), out)
            return out
        pg = self._pg_parts()
        out = out.with_suffix(".dump")
        env = {**os.environ, "PGPASSWORD": pg["password"]}
        cmd = ["pg_dump", "-h", pg["host"], "-p", pg["port"], "-U", pg["user"], "-Fc", "-f", str(out), pg["dbname"]]
        try:
            proc = await asyncio.to_thread(subprocess.run, cmd, env=env, capture_output=True, text=True)
        except FileNotFoundError:
            raise ValidationFailed("pg_dump not found on PATH. Install the PostgreSQL client tools (on Windows add "
                                   "e.g. C:\\Program Files\\PostgreSQL\\16\\bin to PATH), or use the SQLite database.")
        if proc.returncode != 0:
            raise ValidationFailed(f"pg_dump failed: {proc.stderr.strip()[:500]}")
        return out

    async def _restore_database(self, path: Path, db: AsyncSession, rec_snapshot: dict, *, actor: str | None,
                                now: datetime) -> None:
        """Postgres: pg_restore. SQLite: swap the file while the engine holds no connection, then record the
        restore INTO the restored database via a fresh session. The BackupRecord row does not survive the swap
        (a backup does not contain a record of itself), so it is re-inserted with restored_at set via merge."""
        if self.settings.is_sqlite:
            from app.db.engine import get_session_factory, reset_engine

            dst = self._sqlite_path()
            await db.close()          # release this session's snapshot so the file is not locked (Windows) or stale
            await reset_engine()      # drop every pooled connection to the old file
            for suffix in ("", "-wal", "-shm"):
                Path(str(dst) + suffix).unlink(missing_ok=True)
            await asyncio.to_thread(self._sqlite_online_copy, path, dst)
            async with get_session_factory()() as fresh:  # fresh engine -> sees the restored file
                await fresh.merge(BackupRecord(**{**rec_snapshot, "restored_at": now}))
                await record_audit(fresh, AuditAction.BACKUP_RESTORED, actor=actor, entity_type="backup",
                                   entity_id=rec_snapshot["id"], details={"type": "database", "file": path.name})
                await fresh.commit()
            return
        pg = self._pg_parts()
        env = {**os.environ, "PGPASSWORD": pg["password"]}
        cmd = ["pg_restore", "-h", pg["host"], "-p", pg["port"], "-U", pg["user"], "-d", pg["dbname"], "--clean",
               "--if-exists", "--no-owner", str(path)]
        try:
            proc = await asyncio.to_thread(subprocess.run, cmd, env=env, capture_output=True, text=True)
        except FileNotFoundError:
            raise ValidationFailed("pg_restore not found on PATH. Install the PostgreSQL client tools, or use the "
                                   "SQLite database.")
        if proc.returncode != 0:
            raise ValidationFailed(f"pg_restore failed: {proc.stderr.strip()[:500]}")

    # ------------------------------------------------------------------ tarballs
    def _tar_dir(self, src: Path, name: str) -> Path:
        out = self.settings.backup_root / f"{name}_{self._stamp()}.tar.gz"
        with tarfile.open(out, "w:gz") as tf:
            if src.exists():
                tf.add(src, arcname=src.name)
        out.chmod(0o600)
        return out

    def _backup_config(self) -> Path:
        """Configuration snapshot with secret-looking values redacted."""
        out = self.settings.backup_root / f"config_{self._stamp()}.env"
        lines = []
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    if any(x in k.upper() for x in REDACT_KEYS):
                        v = "<redacted>"
                    lines.append(f"{k}={v}")
                else:
                    lines.append(line)
        lines.append(f"# capacity={self.settings.capacity.model_dump()}")
        out.write_text("\n".join(lines), encoding="utf-8")
        out.chmod(0o600)
        return out

    # ------------------------------------------------------------------ public API
    async def backup(self, db: AsyncSession, backup_type: str, *, actor: str | None = None, notes: str | None = None) -> BackupRecord:
        if backup_type not in BACKUP_TYPES:
            raise ValidationFailed(f"backup_type must be one of {BACKUP_TYPES}")
        if backup_type == "database":
            path = await self._backup_database()
        elif backup_type == "sessions":
            path = await asyncio.to_thread(self._tar_dir, self.settings.sessions_root, "sessions")
        elif backup_type == "evidence":
            path = await asyncio.to_thread(self._tar_dir, self.settings.evidence_root, "evidence")
        else:
            path = self._backup_config()
        rec = BackupRecord(backup_type=backup_type, file_path=str(path.resolve()), sha256=sha256_file(path),
                           size_bytes=path.stat().st_size, created_by=actor, created_at=datetime.now(timezone.utc), notes=notes)
        db.add(rec)
        await db.flush()
        await record_audit(db, AuditAction.BACKUP_CREATED, actor=actor, entity_type="backup", entity_id=rec.id,
                           details={"type": backup_type, "file": path.name, "sha256": rec.sha256})
        return rec

    async def verify(self, db: AsyncSession, backup_id: str) -> bool:
        rec = await db.get(BackupRecord, backup_id)
        if not rec:
            raise NotFoundError("backup not found")
        p = Path(rec.file_path)
        return p.exists() and sha256_file(p) == rec.sha256

    async def restore(self, db: AsyncSession, backup_id: str, *, actor: str | None = None) -> BackupRecord:
        rec = await db.get(BackupRecord, backup_id)
        if not rec:
            raise NotFoundError("backup not found")
        if not await self.verify(db, backup_id):
            raise ValidationFailed("backup integrity check failed; refusing to restore")
        p = Path(rec.file_path)
        now = datetime.now(timezone.utc)
        if rec.backup_type == "database":
            # For SQLite this closes the session, swaps the file and records the restore in a fresh session,
            # because the pre-restore ORM row and audit would not survive replacing the whole database file.
            snapshot = {c.key: getattr(rec, c.key) for c in BackupRecord.__table__.columns}
            await self._restore_database(p, db, snapshot, actor=actor, now=now)
            if self.settings.is_sqlite:
                fresh = await self.get_backup_after_restore(rec.id)
                return fresh or rec
        elif rec.backup_type in ("sessions", "evidence"):
            root = self.settings.sessions_root if rec.backup_type == "sessions" else self.settings.evidence_root
            with tarfile.open(p, "r:gz") as tf:
                tf.extractall(root.parent, filter="data")
        else:
            raise ValidationFailed("config backups are for reference; apply them manually")
        rec.restored_at = now
        await record_audit(db, AuditAction.BACKUP_RESTORED, actor=actor, entity_type="backup", entity_id=rec.id,
                           details={"type": rec.backup_type, "file": p.name})
        return rec

    async def get_backup_after_restore(self, backup_id: str) -> BackupRecord | None:
        from app.db.engine import get_session_factory

        async with get_session_factory()() as fresh:
            return await fresh.get(BackupRecord, backup_id)

    async def list_backups(self, db: AsyncSession) -> list[BackupRecord]:
        return list((await db.execute(select(BackupRecord).order_by(BackupRecord.created_at.desc()))).scalars().all())

    async def integrity_check(self, db: AsyncSession) -> dict:
        """DB reachability + registry/disk reconciliation summary + backup file hashes."""
        from sqlalchemy import text

        await db.execute(text("SELECT 1"))
        sessions = (await db.execute(select(TelegramSession))).scalars().all()
        missing = [s.file_name for s in sessions if not Path(s.file_path).exists()]
        backups = await self.list_backups(db)
        bad = [b.id for b in backups if not (Path(b.file_path).exists() and sha256_file(Path(b.file_path)) == b.sha256)]
        return {"database_ok": True, "sessions_registered": len(sessions), "session_files_missing": missing,
                "backups": len(backups), "backups_corrupt_or_missing": bad}


async def recover(db: AsyncSession, *, actor: str | None = None) -> dict:
    """Crash recovery sequence (§31): reconcile session files with the registry and resume pending jobs."""
    from app.db.engine import get_session_factory
    from app.services.sessions import discover_sessions
    from app.workers.queue import JobQueue

    rep = await discover_sessions(db, actor=actor)
    resumed = await JobQueue(get_session_factory()).resume_stale_running()
    await record_audit(db, AuditAction.SETTINGS_CHANGED, actor=actor, entity_type="system",
                       details={"op": "recover", "discovered": rep.discovered, "missing": rep.missing, "jobs_resumed": resumed})
    return {"sessions_discovered": rep.discovered, "sessions_new": rep.new, "sessions_missing": rep.missing,
            "jobs_resumed": resumed}
