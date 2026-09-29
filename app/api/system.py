"""Exports, audit, errors, monitoring, jobs, backups."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_queue
from app.backup.manager import BackupManager, recover
from app.db.models import ErrorRecord, ExportRecord, User
from app.monitoring.metrics import full_snapshot
from app.schemas import AuditOut, BackupOut, ErrorOut, ExportIn, ExportOut, JobOut, Page
from app.security import rbac
from app.services import analytics
from app.services import cases as case_service
from app.services import exports as export_service
from app.services.audit import search_audit
from app.services.errors import NotFoundError
from app.workers.queue import JobQueue

exports_router = APIRouter(prefix="/exports", tags=["exports"])
audit_router = APIRouter(prefix="/audit", tags=["audit"])
errors_router = APIRouter(prefix="/errors", tags=["errors"])
monitoring_router = APIRouter(prefix="/monitoring", tags=["monitoring"])
jobs_router = APIRouter(prefix="/jobs", tags=["jobs"])
backups_router = APIRouter(prefix="/backups", tags=["backups"])


@exports_router.get("", response_model=list[ExportOut])
async def list_exports(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_REPORTS_EXPORT))):
    return await export_service.list_exports(db)


@exports_router.post("", response_model=ExportOut, status_code=201)
async def create_export(body: ExportIn, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_REPORTS_EXPORT))):
    return await export_service.export_records(db, body.export_type, body.fmt, filters=body.filters, actor=user.username)


@exports_router.post("/cases/{case_id}/pdf", response_model=ExportOut, status_code=201)
async def case_pdf(case_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_REPORTS_EXPORT))):
    return await export_service.export_case_pdf(db, await case_service.get_case(db, case_id), actor=user.username)


@exports_router.post("/cases/{case_id}/evidence-zip", response_model=ExportOut, status_code=201)
async def evidence_zip(case_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_REPORTS_EXPORT))):
    return await export_service.export_evidence_zip(db, await case_service.get_case(db, case_id), actor=user.username)


@exports_router.post("/cases/{case_id}/dossier", response_model=ExportOut, status_code=201)
async def case_dossier(case_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_REPORTS_EXPORT))):
    """Official case dossier (ZIP): case report, evidence with hashes and custody, submission log and audit trail."""
    return await export_service.export_case_dossier(db, await case_service.get_case(db, case_id), actor=user.username)


@exports_router.get("/{export_id}/download")
async def download(export_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_REPORTS_EXPORT))):
    rec = await db.get(ExportRecord, export_id)
    if not rec or not Path(rec.file_path).exists():
        raise NotFoundError("export not found")
    return FileResponse(rec.file_path, filename=Path(rec.file_path).name)


@audit_router.get("", response_model=Page)
async def audit(action: str | None = None, actor: str | None = None, entity_id: str | None = None,
                case_id: str | None = None, session_id: str | None = None, since: datetime | None = None,
                until: datetime | None = None, limit: int = Query(200, le=2000), offset: int = 0,
                db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_AUDIT_READ))):
    rows, total = await search_audit(db, action=action, actor=actor, entity_id=entity_id, case_id=case_id,
                                     session_id=session_id, since=since, until=until, limit=limit, offset=offset)
    return Page(total=total, limit=limit, offset=offset, items=[AuditOut.model_validate(r) for r in rows])


@errors_router.get("", response_model=list[ErrorOut])
async def errors(category: str | None = None, limit: int = Query(200, le=2000), db: AsyncSession = Depends(get_db),
                 _: User = Depends(rbac.require(rbac.P_MONITORING_READ))):
    q = select(ErrorRecord).order_by(ErrorRecord.occurred_at.desc()).limit(limit)
    if category:
        q = q.where(ErrorRecord.category == category)
    return list((await db.execute(q)).scalars().all())


@monitoring_router.get("/metrics")
async def metrics(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_MONITORING_READ))):
    return await full_snapshot(db)


@monitoring_router.get("/analytics")
async def analytics_overview(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_CASES_READ))):
    return await analytics.overview(db)


@jobs_router.get("", response_model=list[JobOut])
async def list_jobs(status: str | None = None, limit: int = 100, queue: JobQueue = Depends(get_queue),
                    _: User = Depends(rbac.require(rbac.P_MONITORING_READ))):
    return await queue.list_jobs(status=status, limit=limit)


@jobs_router.get("/stats")
async def job_stats(queue: JobQueue = Depends(get_queue), _: User = Depends(rbac.require(rbac.P_MONITORING_READ))):
    return await queue.stats()


@jobs_router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, queue: JobQueue = Depends(get_queue), _: User = Depends(rbac.require(rbac.P_SESSIONS_WRITE))):
    return {"cancelled": await queue.cancel(job_id)}


@backups_router.get("", response_model=list[BackupOut])
async def list_backups(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_BACKUPS_MANAGE))):
    return await BackupManager().list_backups(db)


@backups_router.post("/{backup_type}", response_model=BackupOut, status_code=201)
async def create_backup(backup_type: str, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_BACKUPS_MANAGE))):
    return await BackupManager().backup(db, backup_type, actor=user.username)


@backups_router.post("/{backup_id}/verify")
async def verify_backup(backup_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_BACKUPS_MANAGE))):
    return {"ok": await BackupManager().verify(db, backup_id)}


@backups_router.post("/{backup_id}/restore", response_model=BackupOut)
async def restore_backup(backup_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_BACKUPS_MANAGE))):
    return await BackupManager().restore(db, backup_id, actor=user.username)


@backups_router.get("/integrity")
async def integrity(db: AsyncSession = Depends(get_db), _: User = Depends(rbac.require(rbac.P_BACKUPS_MANAGE))):
    return await BackupManager().integrity_check(db)


@backups_router.post("/recover/run")
async def run_recover(db: AsyncSession = Depends(get_db), user: User = Depends(rbac.require(rbac.P_BACKUPS_MANAGE))):
    return await recover(db, actor=user.username)
