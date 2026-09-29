"""Submission lifecycle (§13, §14). One submission per case per channel; status machine PENDING → RUNNING →
COMPLETED / WAITING (operator confirmation) / FAILED → RETRYING / CANCELLED."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.db.models import Case, Evidence, Response, Submission, SubmissionAttempt, Target
from app.domain.enums import AuditAction, CaseStatus, ErrorCategory, ExecutionStatus, SubmissionChannel
from app.services import cases as case_service
from app.services.audit import record_audit
from app.services.errors import NotFoundError, SubmissionError, TransitionError, ValidationFailed, record_error
from app.submission.channels import ReportPackage, get_channel
from app.utils import dumps, loads

OPEN_STATUSES = {ExecutionStatus.PENDING.value, ExecutionStatus.RUNNING.value, ExecutionStatus.WAITING.value,
                 ExecutionStatus.RETRYING.value}
ACCEPTED_OUTCOMES = {"accepted", "actioned", "resolved", "removed", "completed"}
REJECTED_OUTCOMES = {"rejected", "declined", "no_action"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def build_package(db: AsyncSession, case: Case) -> ReportPackage:
    draft = loads(case.draft_json)
    if not draft:
        raise ValidationFailed("case has no approved draft")
    target = await db.get(Target, case.target_id)
    evidence = list((await db.execute(select(Evidence).where(Evidence.case_id == case.id))).scalars().all())
    return ReportPackage(
        case_number=case.case_number, subject=draft.get("subject", ""), summary=draft.get("summary", ""),
        reason=draft.get("reason", ""), evidence_summary=draft.get("evidence_summary", ""),
        requested_review=draft.get("requested_review", ""), reference=draft.get("reference", ""),
        additional_notes=draft.get("additional_notes", ""), legal_basis=case.legal_basis,
        target={"type": target.target_type, "url": target.url, "username": target.username,
                "telegram_id": target.telegram_id, "title": target.title},
        evidence=[{"id": e.id, "type": e.evidence_type, "title": e.title, "sha256": e.sha256, "url": e.external_url}
                  for e in evidence],
    )


async def create_submission(db: AsyncSession, case: Case, *, channel: str, recipient: str | None = None,
                            actor: str | None = None) -> Submission:
    if case.status != CaseStatus.READY.value:
        raise TransitionError(f"case must be 'Ready' to create a submission (current: {case.status})")
    try:
        ch = SubmissionChannel(channel)
    except ValueError as exc:
        raise ValidationFailed(f"unknown channel '{channel}'") from exc
    dup = (await db.execute(select(func.count(Submission.id)).where(
        Submission.case_id == case.id, Submission.channel == ch.value, Submission.status.in_(OPEN_STATUSES)))).scalar_one()
    if dup:
        raise TransitionError("an open submission already exists for this case on this channel")
    package = await build_package(db, case)
    sub = Submission(case_id=case.id, target_id=case.target_id, channel=ch.value, recipient=recipient, operator=actor,
                     package_json=dumps(package.as_dict()), status=ExecutionStatus.PENDING.value)
    db.add(sub)
    await db.flush()
    await case_service._event(db, case, AuditAction.SUBMISSION_CREATED.value, actor,
                              details={"submission_id": sub.id, "channel": ch.value, "recipient": recipient})
    await record_audit(db, AuditAction.SUBMISSION_CREATED, actor=actor, entity_type="submission", entity_id=sub.id,
                       case_id=case.id, provider=ch.value, details={"recipient": recipient})
    return sub


async def get_submission(db: AsyncSession, submission_id: str) -> Submission:
    sub = (await db.execute(select(Submission).where(Submission.id == submission_id)
                            .options(selectinload(Submission.attempts), selectinload(Submission.responses)))).scalar_one_or_none()
    if not sub:
        raise NotFoundError("submission not found")
    return sub


async def execute_submission(db: AsyncSession, sub: Submission, *, approved_by: str, settings: Settings | None = None) -> Submission:
    """Runs the channel adapter once. Requires an explicit approver identity (audited)."""
    settings = settings or get_settings()
    if sub.status not in (ExecutionStatus.PENDING.value, ExecutionStatus.RETRYING.value, ExecutionStatus.FAILED.value):
        raise TransitionError(f"submission is {sub.status}; cannot execute")
    attempts = (await db.execute(select(func.count(SubmissionAttempt.id)).where(SubmissionAttempt.submission_id == sub.id))).scalar_one()
    if attempts >= settings.capacity.max_retries + 1:
        sub.status = ExecutionStatus.FAILED.value
        raise TransitionError("maximum attempts reached for this submission")
    case = await db.get(Case, sub.case_id)
    now = _now()
    attempt = SubmissionAttempt(submission_id=sub.id, attempt_no=attempts + 1, channel=sub.channel, operator=approved_by,
                                started_at=now, status=ExecutionStatus.RUNNING.value)
    db.add(attempt)
    sub.status = ExecutionStatus.RUNNING.value
    sub.started_at = sub.started_at or now
    sub.approved_by = approved_by
    package_dict = loads(sub.package_json)
    package = ReportPackage(**package_dict)
    try:
        outcome = await get_channel(sub.channel, settings).submit(package, sub.recipient, approved_by=approved_by)
    except SubmissionError as exc:
        outcome_status, result, err_code, err_msg, ref, artifact = (ExecutionStatus.FAILED, None,
                                                                    ErrorCategory.SUBMISSION_ERROR.value, exc.message, None, None)
    else:
        outcome_status, result, err_code, err_msg, ref, artifact = (outcome.status, outcome.result, outcome.error_code,
                                                                    outcome.error_message, outcome.reference_number,
                                                                    outcome.artifact_path)
    attempt.finished_at = _now()
    attempt.status = outcome_status.value
    attempt.result = (result or "") + (f" [artifact: {artifact}]" if artifact else "")
    attempt.error_code, attempt.error_message = err_code, err_msg
    sub.status = outcome_status.value
    sub.result = attempt.result
    sub.error_code, sub.error_message = err_code, err_msg
    if ref:
        sub.reference_number = ref
    if outcome_status == ExecutionStatus.COMPLETED:
        sub.finished_at = attempt.finished_at
        if case.status == CaseStatus.READY.value:
            await case_service.transition(db, case, CaseStatus.SUBMITTED.value, actor=approved_by)
        await case_service.transition(db, case, CaseStatus.AWAITING_RESPONSE.value, actor=approved_by)
        if ref and not case.reference_number:
            case.reference_number = ref
        await record_audit(db, AuditAction.SUBMISSION_SENT, actor=approved_by, entity_type="submission", entity_id=sub.id,
                           case_id=case.id, provider=sub.channel, result="COMPLETED", details={"reference": ref})
    elif outcome_status == ExecutionStatus.WAITING:
        if case.status == CaseStatus.READY.value:
            await case_service.transition(db, case, CaseStatus.SUBMITTED.value, actor=approved_by,
                                          note="awaiting operator confirmation")
    elif outcome_status == ExecutionStatus.FAILED:
        await record_error(db, err_code or ErrorCategory.SUBMISSION_ERROR, err_msg or "submission failed",
                           component="submissions", case_id=case.id, submission_id=sub.id, operator=approved_by,
                           provider=sub.channel)
        if attempts + 1 >= settings.capacity.max_retries + 1:
            sub.finished_at = attempt.finished_at
            if case.status in (CaseStatus.READY.value, CaseStatus.SUBMITTED.value):
                if case.status == CaseStatus.READY.value:
                    await case_service.transition(db, case, CaseStatus.SUBMITTED.value, actor=approved_by)
                await case_service.transition(db, case, CaseStatus.FAILED.value, actor=approved_by, note=err_msg)
        else:
            sub.status = ExecutionStatus.RETRYING.value
    return sub


async def confirm_manual_sent(db: AsyncSession, sub: Submission, *, reference_number: str | None, actor: str,
                              note: str | None = None) -> Submission:
    if sub.status != ExecutionStatus.WAITING.value:
        raise TransitionError("only submissions awaiting operator confirmation can be confirmed")
    case = await db.get(Case, sub.case_id)
    sub.status = ExecutionStatus.COMPLETED.value
    sub.finished_at = _now()
    sub.reference_number = reference_number or sub.reference_number
    sub.result = (sub.result or "") + f" | confirmed sent by {actor}" + (f": {note}" if note else "")
    if case.status == CaseStatus.READY.value:
        await case_service.transition(db, case, CaseStatus.SUBMITTED.value, actor=actor)
    if case.status == CaseStatus.SUBMITTED.value:
        await case_service.transition(db, case, CaseStatus.AWAITING_RESPONSE.value, actor=actor)
    if reference_number and not case.reference_number:
        case.reference_number = reference_number
    await record_audit(db, AuditAction.SUBMISSION_SENT, actor=actor, entity_type="submission", entity_id=sub.id,
                       case_id=case.id, provider=sub.channel, result="COMPLETED", reason=note,
                       details={"reference": reference_number, "manual_confirmation": True})
    return sub


async def record_response(db: AsyncSession, sub: Submission, *, outcome: str, body: str | None = None,
                          reference_number: str | None = None, source: str | None = None, actor: str) -> Response:
    if sub.status not in (ExecutionStatus.COMPLETED.value, ExecutionStatus.WAITING.value):
        raise TransitionError("responses can only be recorded for sent submissions")
    case = await db.get(Case, sub.case_id)
    resp = Response(submission_id=sub.id, received_at=_now(), source=source, outcome=outcome.lower(),
                    reference_number=reference_number, body=body, recorded_by=actor)
    db.add(resp)
    await db.flush()
    o = outcome.lower()
    if case.status == CaseStatus.SUBMITTED.value:
        await case_service.transition(db, case, CaseStatus.AWAITING_RESPONSE.value, actor=actor)
    if case.status == CaseStatus.AWAITING_RESPONSE.value:
        if o in ACCEPTED_OUTCOMES:
            await case_service.transition(db, case, CaseStatus.COMPLETED.value, actor=actor, note=o)
        elif o in REJECTED_OUTCOMES:
            await case_service.transition(db, case, CaseStatus.FAILED.value, actor=actor, note=o)
    await case_service._event(db, case, AuditAction.RESPONSE_RECEIVED.value, actor,
                              details={"submission_id": sub.id, "outcome": o, "reference": reference_number})
    await record_audit(db, AuditAction.RESPONSE_RECEIVED, actor=actor, entity_type="submission", entity_id=sub.id,
                       case_id=case.id, provider=sub.channel, result=o, details={"reference": reference_number})
    return resp


async def cancel_submission(db: AsyncSession, sub: Submission, *, actor: str, reason: str | None = None) -> Submission:
    if sub.status not in (ExecutionStatus.PENDING.value, ExecutionStatus.WAITING.value, ExecutionStatus.RETRYING.value):
        raise TransitionError(f"cannot cancel a {sub.status} submission")
    sub.status = ExecutionStatus.CANCELLED.value
    sub.finished_at = _now()
    sub.result = f"cancelled by {actor}: {reason or ''}"
    case = await db.get(Case, sub.case_id)
    if case.status == CaseStatus.SUBMITTED.value:
        await case_service.transition(db, case, CaseStatus.FAILED.value, actor=actor, note="submission cancelled")
    await record_audit(db, AuditAction.CASE_STATUS_CHANGED, actor=actor, entity_type="submission", entity_id=sub.id,
                       case_id=case.id, result="CANCELLED", reason=reason)
    return sub


async def list_submissions(db: AsyncSession, *, status: str | None = None, case_id: str | None = None,
                           channel: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[Submission], int]:
    q = select(Submission).options(selectinload(Submission.attempts), selectinload(Submission.responses))
    if status:
        q = q.where(Submission.status == status)
    if case_id:
        q = q.where(Submission.case_id == case_id)
    if channel:
        q = q.where(Submission.channel == channel)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Submission.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return list(rows), total


async def submission_stats(db: AsyncSession) -> dict:
    rows = (await db.execute(select(Submission.status, func.count()).group_by(Submission.status))).all()
    out = {s.value: 0 for s in ExecutionStatus}
    out.update({r[0]: r[1] for r in rows})
    return out
