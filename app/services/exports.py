"""Exports (§19): JSON / CSV for every registry, PDF case report, ZIP evidence package. Each export is recorded
and audited. Sensitive fields (absolute session paths, password hashes, secrets) are never exported."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import (AuditLog, Case, ErrorRecord, Evidence, ExportRecord, Submission, Target, TelegramSession)
from app.domain.enums import AuditAction
from app.services.audit import record_audit
from app.services.errors import ValidationFailed
from app.services.evidence import build_evidence_package, list_evidence
from app.utils import dumps, loads, sha256_file

EXPORTABLE = {
    "sessions": (TelegramSession, ("id", "file_name", "location", "file_format", "encrypted", "username", "telegram_id",
                                   "phone_masked", "status", "health", "enabled", "last_check", "last_success_at",
                                   "last_failure_at", "last_error", "last_error_category", "failure_count",
                                   "consecutive_failures", "availability", "group_id", "proxy_id", "created_at", "updated_at")),
    "targets": (Target, ("id", "target_type", "url", "username", "telegram_id", "title", "description", "status", "tags",
                         "created_by", "created_at", "updated_at")),
    "cases": (Case, ("id", "case_number", "target_id", "reason_id", "title", "explanation", "legal_basis",
                     "reference_number", "priority", "status", "assigned_operator", "created_by", "approved_by",
                     "approved_at", "closed_at", "created_at", "updated_at")),
    "submissions": (Submission, ("id", "case_id", "target_id", "channel", "recipient", "operator", "status", "started_at",
                                 "finished_at", "result", "error_code", "error_message", "reference_number",
                                 "approved_by", "created_at")),
    "evidence": (Evidence, ("id", "case_id", "target_id", "evidence_type", "title", "description", "original_name",
                            "mime_type", "size_bytes", "sha256", "external_url", "captured_at", "added_by",
                            "integrity_ok", "last_verified_at", "created_at")),
    "audit": (AuditLog, ("id", "action", "actor", "entity_type", "entity_id", "session_id", "case_id", "provider",
                         "result", "reason", "details_json", "ip_address", "at")),
    "errors": (ErrorRecord, ("id", "category", "message", "component", "session_id", "case_id", "submission_id",
                             "operator", "provider", "details_json", "occurred_at")),
}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def _record(db: AsyncSession, export_type: str, fmt: str, path: Path, actor: str | None, filters: dict | None) -> ExportRecord:
    rec = ExportRecord(export_type=export_type, fmt=fmt, file_path=str(path.resolve()), sha256=sha256_file(path),
                       size_bytes=path.stat().st_size, requested_by=actor, filters_json=dumps(filters) if filters else None,
                       created_at=datetime.now(timezone.utc))
    db.add(rec)
    await db.flush()
    await record_audit(db, AuditAction.EXPORT_CREATED, actor=actor, entity_type="export", entity_id=rec.id,
                       details={"type": export_type, "fmt": fmt, "file": path.name, "sha256": rec.sha256})
    return rec


async def export_records(db: AsyncSession, export_type: str, fmt: str = "json", *, filters: dict | None = None,
                         actor: str | None = None, settings: Settings | None = None) -> ExportRecord:
    if export_type not in EXPORTABLE:
        raise ValidationFailed(f"unknown export type '{export_type}'. Known: {', '.join(EXPORTABLE)}")
    if fmt not in ("json", "csv"):
        raise ValidationFailed("fmt must be json or csv")
    settings = settings or get_settings()
    settings.ensure_dirs()
    model, columns = EXPORTABLE[export_type]
    q = select(model)
    for k, v in (filters or {}).items():
        if hasattr(model, k) and v is not None:
            q = q.where(getattr(model, k) == v)
    rows = (await db.execute(q)).scalars().all()
    data = [{c: getattr(r, c) for c in columns} for r in rows]
    path = settings.export_root / f"{export_type}_{_stamp()}.{fmt}"
    if fmt == "json":
        path.write_text(dumps({"export_type": export_type, "generated_at": _stamp(), "count": len(data), "rows": data}),
                        encoding="utf-8")
    else:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(columns))
        w.writeheader()
        for row in data:
            w.writerow({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()})
        path.write_text(buf.getvalue(), encoding="utf-8")
    path.chmod(0o600)
    return await _record(db, export_type, fmt, path, actor, filters)


async def export_case_pdf(db: AsyncSession, case: Case, *, actor: str | None = None,
                          settings: Settings | None = None) -> ExportRecord:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib import colors

    settings = settings or get_settings()
    settings.ensure_dirs()
    target = await db.get(Target, case.target_id)
    evidence = await list_evidence(db, case.id)
    subs = list((await db.execute(select(Submission).where(Submission.case_id == case.id))).scalars().all())
    draft = loads(case.draft_json) or {}
    path = settings.export_root / f"case_{case.case_number}_{_stamp()}.pdf"
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm)

    def p(text: str, style="BodyText"):
        return Paragraph(str(text).replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br/>"), styles[style])

    story = [p(f"Case report {case.case_number}", "Title"), p(case.title, "Heading2"), Spacer(1, 6)]
    meta = [["Status", case.status], ["Priority", case.priority], ["Assigned", case.assigned_operator or "-"],
            ["Created", case.created_at.isoformat()], ["Approved by", f"{case.approved_by or '-'} {case.approved_at or ''}"],
            ["Reference", case.reference_number or "-"], ["Legal basis", case.legal_basis or "-"],
            ["Target", f"{target.target_type}: {target.url or target.username or target.telegram_id}"]]
    t = Table(meta, colWidths=[4 * cm, 12 * cm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke)]))
    story += [t, Spacer(1, 10)]
    for key in ("subject", "summary", "reason", "evidence_summary", "requested_review", "reference", "additional_notes"):
        if draft.get(key):
            story += [p(key.replace("_", " ").title(), "Heading3"), p(draft[key])]
    story += [p("Evidence", "Heading3")]
    rows = [["Type", "Title", "SHA-256", "Integrity"]] + [[e.evidence_type, e.title[:40], (e.sha256 or "")[:24] + "…",
                                                           "OK" if e.integrity_ok else "?"] for e in evidence]
    et = Table(rows, colWidths=[2.5 * cm, 6 * cm, 5 * cm, 2 * cm])
    et.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    story += [et, Spacer(1, 10), p("Submissions", "Heading3")]
    for s in subs:
        story += [p(f"{s.channel} → {s.recipient or '-'} | {s.status} | ref {s.reference_number or '-'} | {s.result or ''}")]
    story += [Spacer(1, 10), p("History", "Heading3")]
    for ev in case.events:
        story += [p(f"{ev.created_at.isoformat()} — {ev.event_type} — {ev.actor or '-'} "
                    f"{('(' + (ev.from_status or '') + ' → ' + (ev.to_status or '') + ')') if ev.to_status else ''}")]
    doc.build(story)
    path.chmod(0o600)
    return await _record(db, "case_pdf", "pdf", path, actor, {"case_id": case.id})


async def export_evidence_zip(db: AsyncSession, case: Case, *, actor: str | None = None,
                              settings: Settings | None = None) -> ExportRecord:
    path = await build_evidence_package(db, case, actor=actor, settings=settings)
    return await _record(db, "evidence_zip", "zip", path, actor, {"case_id": case.id})


async def list_exports(db: AsyncSession, limit: int = 100) -> list[ExportRecord]:
    return list((await db.execute(select(ExportRecord).order_by(ExportRecord.created_at.desc()).limit(limit))).scalars().all())
