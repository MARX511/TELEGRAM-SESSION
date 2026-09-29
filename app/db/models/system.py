from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class ErrorRecord(IdMixin, Base):
    """§16: categorised errors, linked to session/case/submission when relevant."""

    __tablename__ = "errors"
    __table_args__ = (Index("ix_errors_category_at", "category", "occurred_at"),)

    category: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    component: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(32), index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), index=True)
    submission_id: Mapped[str | None] = mapped_column(String(32))
    operator: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))
    details_json: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(IdMixin, Base):
    """§15/§28: what / when / which session / which case / operator / provider / result / why failed."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_action_at", "action", "at"), Index("ix_audit_actor_at", "actor", "at"))

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(32), index=True)
    session_id: Mapped[str | None] = mapped_column(String(32))
    case_id: Mapped[str | None] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(32))
    result: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobRecord(IdMixin, TimestampMixin, Base):
    """Persistent queue record so pending jobs survive a crash and can be resumed (§31)."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)

    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker: Mapped[str | None] = mapped_column(String(64))
    result_json: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str | None] = mapped_column(String(64))


class ExportRecord(IdMixin, Base):
    __tablename__ = "exports"

    export_type: Mapped[str] = mapped_column(String(32), nullable=False)
    fmt: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    requested_by: Mapped[str | None] = mapped_column(String(64))
    filters_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BackupRecord(IdMixin, Base):
    __tablename__ = "backups"

    backup_type: Mapped[str] = mapped_column(String(32), nullable=False)  # database | sessions | config | evidence
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
