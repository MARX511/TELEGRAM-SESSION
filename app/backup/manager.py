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

    async def _backup_database(self) -> Path:
        out = self.settings.backup_root / f"db_{self._stamp()}"
        if self.settings.is_sqlite:
            src = Path(self.settings.database_url.split("///", 1)[1])
            out = out.with_suffix(".sqlite")
            shutil.copy2(src, out)
            return out
        pg = self._pg_parts()
        out = out.with_suffix(".dump")
        env = {**os.environ, "PGPASSWORD": pg["password"]}
        cmd = ["pg_dump", "-h", pg["host"], "-p", pg["port"], "-U", pg["user"], "-Fc", "-f", str(out), pg["dbname"]]
        proc = await asyncio.to_thread(subprocess.run, cmd, env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            raise ValidationFailed(f"pg_dump failed: {proc.stderr.strip()[:500]}")
        return out

    async def _restore_database(self, path: Path) -> None:
        if self.settings.is_sqlite:
            dst = Path(self.settings.database_url.split("///", 1)[1])
            shutil.copy2(path, dst)
            return
        pg = self._pg_parts()
        env = {**os.environ, "PGPASSWORD": pg["password"]}
        cmd = ["pg_restore", "-h", pg["host"], "-p", pg["port"], "-U", pg["user"], "-d", pg["dbname"], "--clean",
               "--if-exists", "--no-owner", str(path)]
        proc = await asyncio.to_thread(subprocess.run, cmd, env=env, capture_output=True, text=True)
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
        if rec.backup_type == "database":
            await self._restore_database(p)
        elif rec.backup_type in ("sessions", "evidence"):
            root = self.settings.sessions_root if rec.backup_type == "sessions" else self.settings.evidence_root
            with tarfile.open(p, "r:gz") as tf:
                tf.extractall(root.parent, filter="data")
        else:
            raise ValidationFailed("config backups are for reference; apply them manually")
        rec.restored_at = datetime.now(timezone.utc)
        await record_audit(db, AuditAction.BACKUP_RESTORED, actor=actor, entity_type="backup", entity_id=rec.id,
                           details={"type": rec.backup_type, "file": p.name})
        return rec

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
