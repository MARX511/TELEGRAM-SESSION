from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, UTCDateTime, TimestampMixin


class Evidence(IdMixin, TimestampMixin, Base):
    """§12: evidence with integrity hash + chain of custody."""

    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_case_type", "case_id", "evidence_type"),)

    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    target_id: Mapped[str | None] = mapped_column(ForeignKey("targets.id", ondelete="SET NULL"))
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    storage_path: Mapped[str | None] = mapped_column(String(1024))  # inside EVIDENCE_ROOT, never exposed raw
    original_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    external_url: Mapped[str | None] = mapped_column(String(1024))
    captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    added_by: Mapped[str | None] = mapped_column(String(64))
    integrity_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    custody: Mapped[list["EvidenceCustody"]] = relationship(back_populates="evidence", cascade="all, delete-orphan",
                                                           order_by="EvidenceCustody.at")


class EvidenceCustody(IdMixin, Base):
    __tablename__ = "evidence_custody"

    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # added | verified | exported | accessed | moved
    actor: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sha256_at_time: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)

    evidence: Mapped[Evidence] = relationship(back_populates="custody")
