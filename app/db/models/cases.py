from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, UTCDateTime, TimestampMixin
from app.domain.enums import CasePriority, CaseStatus


class Reason(IdMixin, TimestampMixin, Base):
    """§10: report reason catalog, data-driven and versioned. Never hard-coded."""

    __tablename__ = "reasons"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_reason_code_version"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    policy_reference: Mapped[str | None] = mapped_column(String(255))
    default_explanation_template: Mapped[str | None] = mapped_column(Text)
    official_channel_hint: Mapped[str | None] = mapped_column(String(255))  # e.g. abuse@telegram.org
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class ExplanationTemplate(IdMixin, TimestampMixin, Base):
    """§11: per case-type template; operator edits the rendered text before approval."""

    __tablename__ = "explanation_templates"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    requested_review: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    additional_notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Case(IdMixin, TimestampMixin, Base):
    __tablename__ = "cases"
    __table_args__ = (Index("ix_cases_status_priority", "status", "priority"),)

    case_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id", ondelete="RESTRICT"), nullable=False, index=True)
    reason_id: Mapped[str | None] = mapped_column(ForeignKey("reasons.id", ondelete="SET NULL"), index=True)
    template_id: Mapped[str | None] = mapped_column(ForeignKey("explanation_templates.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    legal_basis: Mapped[str | None] = mapped_column(Text)
    reference_number: Mapped[str | None] = mapped_column(String(128), index=True)
    priority: Mapped[str] = mapped_column(String(16), default=CasePriority.NORMAL.value, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=CaseStatus.DRAFT.value, nullable=False, index=True)
    assigned_operator: Mapped[str | None] = mapped_column(String(64), index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    draft_json: Mapped[str | None] = mapped_column(Text)  # rendered report package (editable before approval)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    target = relationship("Target")
    reason = relationship("Reason")
    events: Mapped[list["CaseEvent"]] = relationship(back_populates="case", cascade="all, delete-orphan",
                                                    order_by="CaseEvent.created_at")


class CaseEvent(IdMixin, Base):
    """§18: full case history."""

    __tablename__ = "case_events"
    __table_args__ = (Index("ix_case_events_case_created", "case_id", "created_at"),)

    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    details_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    case: Mapped[Case] = relationship(back_populates="events")
