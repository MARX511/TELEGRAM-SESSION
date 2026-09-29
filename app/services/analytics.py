"""Report analytics (§26). Read-only aggregates for dashboards; never used to drive automation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case as sa_case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Case, ErrorRecord, Reason, Response, Submission, TelegramSession
from app.domain.enums import CaseStatus, ExecutionStatus, HealthState


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


async def submissions_by_day(db: AsyncSession, days: int = 30) -> list[dict]:
    day = func.date(Submission.created_at)
    rows = (await db.execute(select(day, func.count()).where(Submission.created_at >= _since(days))
                             .group_by(day).order_by(day))).all()
    return [{"day": str(r[0]), "count": r[1]} for r in rows]


async def submissions_by_reason(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(Reason.code, func.count(Submission.id)).select_from(Submission)
                             .join(Case, Case.id == Submission.case_id).outerjoin(Reason, Reason.id == Case.reason_id)
                             .group_by(Reason.code).order_by(func.count(Submission.id).desc()))).all()
    return [{"reason": r[0] or "unspecified", "count": r[1]} for r in rows]


async def cases_by_status(db: AsyncSession) -> dict:
    rows = (await db.execute(select(Case.status, func.count()).group_by(Case.status))).all()
    out = {s.value: 0 for s in CaseStatus}
    out.update({r[0]: r[1] for r in rows})
    return out


async def submission_success_failure(db: AsyncSession) -> dict:
    rows = (await db.execute(select(Submission.status, func.count()).group_by(Submission.status))).all()
    d = {r[0]: r[1] for r in rows}
    return {"completed": d.get(ExecutionStatus.COMPLETED.value, 0), "failed": d.get(ExecutionStatus.FAILED.value, 0),
            "waiting": d.get(ExecutionStatus.WAITING.value, 0), "pending": d.get(ExecutionStatus.PENDING.value, 0),
            "cancelled": d.get(ExecutionStatus.CANCELLED.value, 0), "by_status": d}


async def average_processing_hours(db: AsyncSession) -> float | None:
    rows = (await db.execute(select(Case.created_at, Case.closed_at, Case.updated_at)
                             .where(Case.status.in_([CaseStatus.COMPLETED.value, CaseStatus.CLOSED.value])))).all()
    durations = [((r[1] or r[2]) - r[0]).total_seconds() / 3600 for r in rows if (r[1] or r[2]) and r[0]]
    return round(sum(durations) / len(durations), 2) if durations else None


async def session_availability(db: AsyncSession) -> dict:
    total = (await db.execute(select(func.count(TelegramSession.id)))).scalar_one()
    avg = (await db.execute(select(func.avg(TelegramSession.availability)))).scalar_one()
    rows = (await db.execute(select(TelegramSession.health, func.count()).group_by(TelegramSession.health))).all()
    by_health = {h.value: 0 for h in HealthState}
    by_health.update({r[0]: r[1] for r in rows})
    return {"total": total, "average_availability": round(float(avg or 0), 3), "by_health": by_health}


async def submission_response_hours(db: AsyncSession) -> float | None:
    rows = (await db.execute(select(Submission.finished_at, func.min(Response.received_at))
                             .join(Response, Response.submission_id == Submission.id)
                             .group_by(Submission.id, Submission.finished_at))).all()
    vals = [(r[1] - r[0]).total_seconds() / 3600 for r in rows if r[0] and r[1]]
    return round(sum(vals) / len(vals), 2) if vals else None


async def failure_causes(db: AsyncSession, days: int = 30) -> list[dict]:
    rows = (await db.execute(select(ErrorRecord.category, func.count()).where(ErrorRecord.occurred_at >= _since(days))
                             .group_by(ErrorRecord.category).order_by(func.count().desc()))).all()
    return [{"category": r[0], "count": r[1]} for r in rows]


async def overview(db: AsyncSession) -> dict:
    return {
        "reports_by_day": await submissions_by_day(db),
        "reports_by_reason": await submissions_by_reason(db),
        "cases_by_status": await cases_by_status(db),
        "success_failure": await submission_success_failure(db),
        "average_processing_hours": await average_processing_hours(db),
        "session_availability": await session_availability(db),
        "submission_response_hours": await submission_response_hours(db),
        "failure_causes": await failure_causes(db),
    }
