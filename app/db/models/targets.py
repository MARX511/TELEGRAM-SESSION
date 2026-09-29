from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.domain.enums import TargetStatus


class Target(IdMixin, TimestampMixin, Base):
    __tablename__ = "targets"
    __table_args__ = (Index("ix_targets_type_status", "target_type", "status"),)

    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024), index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    telegram_id: Mapped[int | None] = mapped_column(Integer, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=TargetStatus.NEW.value, nullable=False)
    tags: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(String(64))
