from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin
from app.domain.enums import ExecutionStatus


class Submission(IdMixin, TimestampMixin, Base):
    """§13/§14: one official submission per case per channel. Never session/account based."""

    __tablename__ = "submissions"
    __table_args__ = (Index("ix_submissions_case_status", "case_id", "status"),)

    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id", ondelete="RESTRICT"), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(255))  # official address / portal name
    operator: Mapped[str | None] = mapped_column(String(64))
    package_json: Mapped[str | None] = mapped_column(Text)  # the report package as sent
    status: Mapped[str] = mapped_column(String(16), default=ExecutionStatus.PENDING.value, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    reference_number: Mapped[str | None] = mapped_column(String(128), index=True)
    approved_by: Mapped[str | None] = mapped_column(String(64))

    attempts: Mapped[list["SubmissionAttempt"]] = relationship(back_populates="submission",
                                                              cascade="all, delete-orphan",
                                                              order_by="SubmissionAttempt.started_at")
    responses: Mapped[list["Response"]] = relationship(back_populates="submission", cascade="all, delete-orphan")


class SubmissionAttempt(IdMixin, Base):
    __tablename__ = "submission_attempts"

    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    operator: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)

    submission: Mapped[Submission] = relationship(back_populates="attempts")


class Response(IdMixin, Base):
    __tablename__ = "responses"

    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str | None] = mapped_column(String(64))  # accepted | rejected | info_requested | ...
    reference_number: Mapped[str | None] = mapped_column(String(128))
    body: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str | None] = mapped_column(String(64))

    submission: Mapped[Submission] = relationship(back_populates="responses")
