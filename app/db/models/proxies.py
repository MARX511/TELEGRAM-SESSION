from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, UTCDateTime, TimestampMixin
from app.domain.enums import ProxyStatus


class Proxy(IdMixin, TimestampMixin, Base):
    """§4: routing / network isolation only. No rotation-for-evasion logic exists anywhere."""

    __tablename__ = "proxies"
    __table_args__ = (UniqueConstraint("host", "port", "protocol", name="uq_proxy_endpoint"),)

    name: Mapped[str | None] = mapped_column(String(64))
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)
    username: Mapped[str | None] = mapped_column(String(128))
    secret_ref: Mapped[str | None] = mapped_column(String(128))  # reference to secret store, never plaintext
    status: Mapped[str] = mapped_column(String(16), default=ProxyStatus.UNKNOWN.value, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_check: Mapped[datetime | None] = mapped_column(UTCDateTime())
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
