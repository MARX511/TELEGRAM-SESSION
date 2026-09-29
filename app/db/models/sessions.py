from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, UTCDateTime, TimestampMixin
from app.domain.enums import HealthState, SessionLocation, SessionStatus


class SessionGroup(IdMixin, TimestampMixin, Base):
    """§5: groups allow bulk admin actions (check/disable/monitor) and per-group settings."""

    __tablename__ = "session_groups"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    proxy_id: Mapped[str | None] = mapped_column(ForeignKey("proxies.id", ondelete="SET NULL"))
    check_concurrency: Mapped[int | None] = mapped_column(Integer)  # per-group override (§5/§39)
    settings_json: Mapped[str | None] = mapped_column(Text)

    sessions: Mapped[list["TelegramSession"]] = relationship(back_populates="group")


class Account(IdMixin, TimestampMixin, Base):
    """Telegram account identity resolved from a validated session (§1)."""

    __tablename__ = "accounts"

    telegram_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    phone_masked: Mapped[str | None] = mapped_column(String(32))
    display_name: Mapped[str | None] = mapped_column(String(128))
    proxy_id: Mapped[str | None] = mapped_column(ForeignKey("proxies.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)


class TelegramSession(IdMixin, TimestampMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("file_path", name="uq_sessions_file_path"),
        Index("ix_sessions_status_location", "status", "location"),
    )

    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    file_size: Mapped[int | None] = mapped_column(Integer)
    file_format: Mapped[str | None] = mapped_column(String(32))  # telethon | pyrogram | unknown
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    location: Mapped[str] = mapped_column(String(16), default=SessionLocation.ACTIVE.value, nullable=False)

    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("session_groups.id", ondelete="SET NULL"), index=True)
    proxy_id: Mapped[str | None] = mapped_column(ForeignKey("proxies.id", ondelete="SET NULL"))

    username: Mapped[str | None] = mapped_column(String(64), index=True)
    telegram_id: Mapped[int | None] = mapped_column(Integer, index=True)
    phone_masked: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[str] = mapped_column(String(32), default=SessionStatus.UNCHECKED.value, nullable=False, index=True)
    health: Mapped[str] = mapped_column(String(16), default=HealthState.UNAVAILABLE.value, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_check: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_category: Mapped[str | None] = mapped_column(String(32))
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    check_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    availability: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # success ratio 0..1
    rate_limited_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    tags: Mapped[str | None] = mapped_column(String(255))

    group: Mapped[SessionGroup | None] = relationship(back_populates="sessions")
    account: Mapped[Account | None] = relationship()
    checks: Mapped[list["SessionCheck"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class SessionCheck(IdMixin, Base):
    __tablename__ = "session_checks"
    __table_args__ = (Index("ix_session_checks_session_started", "session_id", "started_at"),)

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    operator: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    result_status: Mapped[str] = mapped_column(String(32), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    server_wait_seconds: Mapped[int | None] = mapped_column(Integer)  # FloodWait as told by server (§7)
    details_json: Mapped[str | None] = mapped_column(Text)

    session: Mapped[TelegramSession] = relationship(back_populates="checks")
