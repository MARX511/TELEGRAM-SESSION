from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.db.models import User
from app.schemas import ConfirmIn, Page, ResponseIn, ResponseOut, SubmissionCreate, SubmissionOut
from app.security import rbac
from app.services import cases as case_service
from app.services import submissions as svc
from app.submission.channels import OFFICIAL_EMAIL_RECIPIENTS, OFFICIAL_PORTALS

router = APIRouter(prefix="/submissions", tags=["submissions"])


@router.get("", response_model=Page)
async def list_submissions(status: str | None = None, case_id: str | None = None, channel: str | None = None,
                           limit: int = Query(100, le=1000), offset: int = 0, db: AsyncSession = Depends(get_db),
                           _: User = Depends(rbac.require(rbac.P_SUBMISSIONS_READ))):
    rows, total = await svc.list_submissions(db, status=status, case_id=case_id, channel=channel, limit=limit, offset=offset)
    return Page(total=total, limit=limit, offset=offset, items=[SubmissionOut.model_validate(r) for r in rows])


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_SUBMISSIONS_READ))):
    return await svc.submission_stats(db)


@router.get("/channels")
async def channels(_: User = Depends(rbac.require(rbac.P_SUBMISSIONS_READ))):
    return {"manual": "operator files via official channel and confirms", "official_portal": OFFICIAL_PORTALS,
            "official_email": OFFICIAL_EMAIL_RECIPIENTS, "official_api": "not available (no documented public API)"}


@router.post("/cases/{case_id}", response_model=SubmissionOut, status_code=201)
async def create(case_id: str, body: SubmissionCreate, db: AsyncSession = Depends(get_db),
                 user: User = Depends(rbac.require(rbac.P_SUBMISSIONS_WRITE))):
    case = await case_service.get_case(db, case_id)
    sub = await svc.create_submission(db, case, channel=body.channel, recipient=body.recipient, actor=user.username)
    return await svc.get_submission(db, sub.id)


@router.get("/{submission_id}", response_model=SubmissionOut)
async def get_one(submission_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_SUBMISSIONS_READ))):
    return await svc.get_submission(db, submission_id)


@router.post("/{submission_id}/execute", response_model=SubmissionOut)
async def execute(submission_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_SUBMISSIONS_EXECUTE))):
    sub = await svc.get_submission(db, submission_id)
    await svc.execute_submission(db, sub, approved_by=user.username)
    return await svc.get_submission(db, sub.id)


@router.post("/{submission_id}/confirm", response_model=SubmissionOut)
async def confirm(submission_id: str, body: ConfirmIn, db: AsyncSession = Depends(get_db),
                  user: User = Depends(rbac.require(rbac.P_SUBMISSIONS_EXECUTE))):
    sub = await svc.get_submission(db, submission_id)
    await svc.confirm_manual_sent(db, sub, reference_number=body.reference_number, actor=user.username, note=body.note)
    return await svc.get_submission(db, sub.id)


@router.post("/{submission_id}/response", response_model=ResponseOut, status_code=201)
async def respond(submission_id: str, body: ResponseIn, db: AsyncSession = Depends(get_db),
                  user: User = Depends(rbac.require(rbac.P_SUBMISSIONS_WRITE))):
    sub = await svc.get_submission(db, submission_id)
    return await svc.record_response(db, sub, actor=user.username, **body.model_dump())


@router.post("/{submission_id}/cancel", response_model=SubmissionOut)
async def cancel(submission_id: str, reason: str | None = None, db: AsyncSession = Depends(get_db),
                 user: User = Depends(rbac.require(rbac.P_SUBMISSIONS_WRITE))):
    sub = await svc.get_submission(db, submission_id)
    await svc.cancel_submission(db, sub, actor=user.username, reason=reason)
    return await svc.get_submission(db, sub.id)
