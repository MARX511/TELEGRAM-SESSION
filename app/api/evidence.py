from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.db.models import User
from app.schemas import EvidenceOut, ReferenceEvidenceIn
from app.security import rbac
from app.services import cases as case_service
from app.services import evidence as svc

router = APIRouter(prefix="/cases/{case_id}/evidence", tags=["evidence"])


@router.get("", response_model=list[EvidenceOut])
async def list_evidence(case_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_EVIDENCE_READ))):
    case = await case_service.get_case(db, case_id)
    return await svc.list_evidence(db, case.id)


@router.post("/file", response_model=EvidenceOut, status_code=201)
async def add_file(case_id: str, file: UploadFile = File(...), evidence_type: str = Form("file"), title: str = Form(...),
                   description: str | None = Form(None), captured_at: datetime | None = Form(None),
                   db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_EVIDENCE_WRITE))):
    case = await case_service.get_case(db, case_id)
    data = await file.read()
    return await svc.add_file_evidence(db, case, evidence_type=evidence_type, title=title, data=data,
                                       original_name=file.filename or "upload", mime_type=file.content_type,
                                       description=description, captured_at=captured_at, actor=user.username)


@router.post("/reference", response_model=EvidenceOut, status_code=201)
async def add_reference(case_id: str, body: ReferenceEvidenceIn, db: AsyncSession = Depends(get_db),
                        user: User = Depends(rbac.require(rbac.P_EVIDENCE_WRITE))):
    case = await case_service.get_case(db, case_id)
    return await svc.add_reference_evidence(db, case, actor=user.username, **body.model_dump())


@router.post("/verify")
async def verify_all(case_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_EVIDENCE_READ))):
    case = await case_service.get_case(db, case_id)
    return await svc.verify_case_evidence(db, case, actor=user.username)


@router.get("/{evidence_id}/download")
async def download(case_id: str, evidence_id: str, db: AsyncSession = Depends(get_db),
                   user: User = Depends(rbac.require(rbac.P_EVIDENCE_READ))):
    ev = await svc.get_evidence(db, evidence_id)
    data = await svc.read_evidence_bytes(db, ev, actor=user.username)
    return Response(content=data, media_type=ev.mime_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{ev.original_name or evidence_id}"'})
