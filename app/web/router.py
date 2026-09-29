"""Server-rendered dashboard (§22–§26). Uses the same services as the JSON API; never touches session files."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_queue, get_session_factory
from app.config import get_settings
from app.db.models import Account, ErrorRecord, User
from app.domain.enums import AuditAction, CaseStatus, EvidenceType, SubmissionChannel, TargetType
from app.monitoring.metrics import full_snapshot
from app.security import rbac
from app.security.auth import COOKIE_NAME, authenticate, create_access_token, get_current_user_optional
from app.services import analytics, cases as case_service, evidence as evidence_service, exports as export_service
from app.services import proxies as proxy_service, reasons as reason_service, sessions as session_service
from app.services import submissions as submission_service, targets as target_service
from app.services.audit import record_audit, search_audit
from app.services.errors import AppError
from app.submission.channels import OFFICIAL_EMAIL_RECIPIENTS, OFFICIAL_PORTALS
from app.utils import loads
from app.workers.queue import JobQueue

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
templates.env.globals.update(now=lambda: datetime.now(timezone.utc), loads=loads, has_permission=rbac.has_permission, rbac=rbac)


def render(request: Request, name: str, user: User | None, **ctx) -> HTMLResponse:
    flash = request.cookies.get("flash")
    resp = templates.TemplateResponse(request, name, {"request": request, "user": user, "flash": flash,
                                                      "settings": get_settings(), **ctx})
    if flash:
        resp.delete_cookie("flash")
    return resp


def redirect(url: str, flash: str | None = None) -> RedirectResponse:
    resp = RedirectResponse(url, status_code=303)
    if flash:
        resp.set_cookie("flash", flash[:200], max_age=10, httponly=True)
    return resp


async def need(request: Request, user: User | None, perm: str | None = None):
    if user is None:
        return redirect(f"/login?next={request.url.path}")
    if perm and not rbac.has_permission(user, perm):
        return redirect("/", f"Permission '{perm}' required")
    return None


# ------------------------------------------------------------------------- auth
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if user:
        return redirect("/")
    return render(request, "login.html", None, next=request.query_params.get("next", "/"))


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"),
                       db: AsyncSession = Depends(get_db)):
    user = await authenticate(db, username, password)
    if not user:
        await record_audit(db, AuditAction.LOGIN_FAILED, actor=username, ip=client_ip(request), result="FAILED")
        return render(request, "login.html", None, error="Invalid credentials", next=next)
    await record_audit(db, AuditAction.LOGIN, actor=user.username, ip=client_ip(request), result="OK")
    resp = redirect(next if next.startswith("/") else "/")
    resp.set_cookie(COOKIE_NAME, create_access_token(user), httponly=True, samesite="lax",
                    secure=get_settings().app_env == "production", max_age=get_settings().access_token_expire_minutes * 60)
    return resp


@router.get("/logout")
async def logout():
    resp = redirect("/login")
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ------------------------------------------------------------------------- dashboard
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user)):
        return r
    return render(request, "dashboard.html", user, sessions=await session_service.session_stats(db),
                  targets=await target_service.target_stats(db), cases=await case_service.case_stats(db),
                  submissions=await submission_service.submission_stats(db),
                  availability=await analytics.session_availability(db), failures=await analytics.failure_causes(db, 7))


# ------------------------------------------------------------------------- sessions
@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request, status: str | None = None, health: str | None = None, location: str | None = None,
                        search: str | None = None, sort: str = "file_name", order: str = "asc", page: int = 1,
                        db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_SESSIONS_READ)):
        return r
    limit = 50
    rows, total = await session_service.list_sessions(db, status=status or None, health=health or None,
                                                      location=location or None, search=search or None, sort=sort,
                                                      order=order, limit=limit, offset=(page - 1) * limit)
    tpl = "partials/sessions_table.html" if request.headers.get("HX-Request") else "sessions.html"
    return render(request, tpl, user, rows=rows, total=total, page=page, pages=max(1, -(-total // limit)),
                  stats=await session_service.session_stats(db), filters={"status": status, "health": health,
                  "location": location, "search": search, "sort": sort, "order": order})


@router.post("/sessions/scan")
async def sessions_scan(request: Request, db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_SESSIONS_WRITE)):
        return r
    rep = await session_service.discover_sessions(db, actor=user.username)
    return redirect("/sessions", f"Scan: {rep.discovered} found, {rep.new} new, {rep.missing} missing")


@router.post("/sessions/check-all")
async def sessions_check_all(request: Request, db: AsyncSession = Depends(get_db), queue: JobQueue = Depends(get_queue),
                             user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_SESSIONS_CHECK)):
        return r
    rows, _t = await session_service.list_sessions(db, location="active", limit=100000)
    n = 0
    for s in rows:
        if s.enabled:
            await queue.enqueue("session.check", {"session_id": s.id}, requested_by=user.username, db=db)
            n += 1
    return redirect("/sessions", f"Queued {n} health checks")


@router.post("/sessions/{session_id}/{action}")
async def session_action(request: Request, session_id: str, action: str, db: AsyncSession = Depends(get_db),
                         user: User | None = Depends(get_current_user_optional)):
    perm = rbac.P_SESSIONS_CHECK if action == "check" else rbac.P_SESSIONS_WRITE
    if (r := await need(request, user, perm)):
        return r
    s = await session_service.get_session(db, session_id)
    try:
        if action == "check":
            chk = await session_service.check_session(db, s, actor=user.username)
            msg = f"{s.file_name}: {chk.result_status}" + (f" ({chk.error_message})" if chk.error_message else "")
        elif action == "disable":
            await session_service.disable_session(db, s, actor=user.username, reason="dashboard")
            msg = f"{s.file_name} disabled"
        elif action == "enable":
            await session_service.enable_session(db, s, actor=user.username, reason="dashboard")
            msg = f"{s.file_name} enabled (re-check required)"
        elif action == "quarantine":
            await session_service.quarantine_session(db, s, actor=user.username, reason="dashboard")
            msg = f"{s.file_name} quarantined"
        else:
            msg = "unknown action"
    except AppError as exc:
        msg = f"Error: {exc.message}"
    return redirect("/sessions", msg)


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request, db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_SESSIONS_READ)):
        return r
    rows = (await db.execute(select(Account).order_by(Account.created_at.desc()))).scalars().all()
    return render(request, "accounts.html", user, rows=rows)


@router.get("/proxies", response_class=HTMLResponse)
async def proxies_page(request: Request, db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_PROXIES_READ)):
        return r
    return render(request, "proxies.html", user, rows=await proxy_service.list_proxies(db))


@router.post("/proxies")
async def proxies_create(request: Request, host: str = Form(...), port: int = Form(...), protocol: str = Form("socks5"),
                         name: str | None = Form(None), db: AsyncSession = Depends(get_db),
                         user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_PROXIES_WRITE)):
        return r
    try:
        await proxy_service.create_proxy(db, host=host, port=port, protocol=protocol, name=name or None, actor=user.username)
        return redirect("/proxies", "Proxy added")
    except AppError as exc:
        return redirect("/proxies", f"Error: {exc.message}")


@router.post("/proxies/{proxy_id}/check")
async def proxies_check(request: Request, proxy_id: str, db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_PROXIES_WRITE)):
        return r
    p = await proxy_service.check_proxy(db, await proxy_service.get_proxy(db, proxy_id), actor=user.username)
    return redirect("/proxies", f"{p.host}:{p.port} → {p.status}")


# ------------------------------------------------------------------------- targets
@router.get("/targets", response_class=HTMLResponse)
async def targets_page(request: Request, search: str | None = None, target_type: str | None = None, page: int = 1,
                       db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_TARGETS_READ)):
        return r
    limit = 50
    rows, total = await target_service.list_targets(db, search=search or None, target_type=target_type or None,
                                                    limit=limit, offset=(page - 1) * limit)
    return render(request, "targets.html", user, rows=rows, total=total, page=page, pages=max(1, -(-total // limit)),
                  stats=await target_service.target_stats(db), types=[t.value for t in TargetType],
                  filters={"search": search, "target_type": target_type})


@router.post("/targets")
async def targets_create(request: Request, target_type: str = Form(...), url: str | None = Form(None),
                         username: str | None = Form(None), telegram_id: str | None = Form(None),
                         title: str | None = Form(None), description: str | None = Form(None), tags: str | None = Form(None),
                         db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_TARGETS_WRITE)):
        return r
    try:
        t = await target_service.create_target(db, target_type=target_type, url=url or None, username=username or None,
                                               telegram_id=int(telegram_id) if telegram_id else None, title=title or None,
                                               description=description or None, tags=tags or None, actor=user.username)
        return redirect("/targets", f"Target {t.id[:8]} added")
    except (AppError, ValueError) as exc:
        return redirect("/targets", f"Error: {getattr(exc, 'message', str(exc))}")


# ------------------------------------------------------------------------- cases
@router.get("/cases", response_class=HTMLResponse)
async def cases_page(request: Request, status: str | None = None, search: str | None = None, page: int = 1,
                     db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_CASES_READ)):
        return r
    limit = 50
    rows, total = await case_service.list_cases(db, status=status or None, search=search or None, limit=limit,
                                                offset=(page - 1) * limit)
    targets, _t = await target_service.list_targets(db, limit=500)
    return render(request, "cases.html", user, rows=rows, total=total, page=page, pages=max(1, -(-total // limit)),
                  stats=await case_service.case_stats(db), statuses=[s.value for s in CaseStatus], targets=targets,
                  reasons=await reason_service.list_reasons(db), filters={"status": status, "search": search})


@router.post("/cases")
async def cases_create(request: Request, target_id: str = Form(...), title: str = Form(...), reason_code: str | None = Form(None),
                       explanation: str | None = Form(None), legal_basis: str | None = Form(None), priority: str = Form("NORMAL"),
                       db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_CASES_WRITE)):
        return r
    try:
        c = await case_service.create_case(db, target_id=target_id, title=title, reason_code=reason_code or None,
                                           explanation=explanation or None, legal_basis=legal_basis or None,
                                           priority=priority, assigned_operator=user.username, actor=user.username)
        return redirect(f"/cases/{c.id}", f"Case {c.case_number} created")
    except AppError as exc:
        return redirect("/cases", f"Error: {exc.message}")


@router.get("/cases/{case_id}", response_class=HTMLResponse)
async def case_detail(request: Request, case_id: str, db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_CASES_READ)):
        return r
    case = await case_service.get_case(db, case_id)
    subs, _t = await submission_service.list_submissions(db, case_id=case.id)
    return render(request, "case_detail.html", user, case=case, draft=loads(case.draft_json) or {},
                  evidence=await evidence_service.list_evidence(db, case.id), submissions=subs,
                  history=await case_service.case_history(db, case), reasons=await reason_service.list_reasons(db),
                  allowed=[s.value for s in __import__("app.domain.enums", fromlist=["CASE_TRANSITIONS"]).CASE_TRANSITIONS[CaseStatus(case.status)]],
                  evidence_types=[e.value for e in EvidenceType], channels=[c.value for c in SubmissionChannel],
                  emails=OFFICIAL_EMAIL_RECIPIENTS, portals=OFFICIAL_PORTALS)


@router.post("/cases/{case_id}/action/{action}")
async def case_action(request: Request, case_id: str, action: str, to_status: str | None = Form(None),
                      note: str | None = Form(None), reason_code: str | None = Form(None),
                      subject: str | None = Form(None), summary: str | None = Form(None), reason: str | None = Form(None),
                      evidence_summary: str | None = Form(None), requested_review: str | None = Form(None),
                      reference: str | None = Form(None), additional_notes: str | None = Form(None),
                      db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    perm = rbac.P_CASES_APPROVE if (action == "transition" and to_status == CaseStatus.APPROVED.value) else rbac.P_CASES_WRITE
    if (r := await need(request, user, perm)):
        return r
    case = await case_service.get_case(db, case_id)
    try:
        if action == "transition":
            await case_service.transition(db, case, to_status, actor=user.username, note=note or None)
            msg = f"Case is now {to_status}"
        elif action == "reason":
            await case_service.set_reason(db, case, reason_code, actor=user.username)
            msg = f"Reason set to {reason_code}"
        elif action == "draft":
            await case_service.generate_draft(db, case, actor=user.username)
            msg = "Draft generated"
        elif action == "edit-draft":
            await case_service.update_draft(db, case, {"subject": subject, "summary": summary, "reason": reason,
                                                       "evidence_summary": evidence_summary, "requested_review": requested_review,
                                                       "reference": reference, "additional_notes": additional_notes},
                                            actor=user.username)
            msg = "Draft saved"
        elif action == "pdf":
            rec = await export_service.export_case_pdf(db, case, actor=user.username)
            msg = f"PDF exported: {os.path.basename(rec.file_path)}"
        elif action == "evidence-zip":
            rec = await export_service.export_evidence_zip(db, case, actor=user.username)
            msg = f"Evidence package: {os.path.basename(rec.file_path)}"
        elif action == "verify-evidence":
            res = await evidence_service.verify_case_evidence(db, case, actor=user.username)
            msg = f"Evidence verified: {res['ok']}/{res['total']} OK"
        else:
            msg = "unknown action"
    except AppError as exc:
        msg = f"Error: {exc.message}"
    return redirect(f"/cases/{case.id}", msg)


@router.post("/cases/{case_id}/evidence")
async def case_evidence_add(request: Request, case_id: str, evidence_type: str = Form("url"), title: str = Form(...),
                            value: str | None = Form(None), description: str | None = Form(None),
                            db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_EVIDENCE_WRITE)):
        return r
    case = await case_service.get_case(db, case_id)
    form = await request.form()
    upload = form.get("file")
    try:
        if upload is not None and getattr(upload, "filename", ""):
            data = await upload.read()
            await evidence_service.add_file_evidence(db, case, evidence_type=evidence_type, title=title, data=data,
                                                     original_name=upload.filename, mime_type=upload.content_type,
                                                     description=description or None, actor=user.username)
        else:
            await evidence_service.add_reference_evidence(db, case, evidence_type=evidence_type, title=title,
                                                          value=value or "", description=description or None,
                                                          actor=user.username)
        msg = "Evidence added"
    except AppError as exc:
        msg = f"Error: {exc.message}"
    return redirect(f"/cases/{case.id}", msg)


@router.post("/cases/{case_id}/submissions")
async def case_submission_create(request: Request, case_id: str, channel: str = Form("manual"),
                                 recipient: str | None = Form(None), execute: str | None = Form(None),
                                 db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_SUBMISSIONS_WRITE)):
        return r
    case = await case_service.get_case(db, case_id)
    try:
        sub = await submission_service.create_submission(db, case, channel=channel, recipient=recipient or None, actor=user.username)
        msg = f"Submission {sub.id[:8]} created"
        if execute and rbac.has_permission(user, rbac.P_SUBMISSIONS_EXECUTE):
            await submission_service.execute_submission(db, sub, approved_by=user.username)
            msg += f" and executed → {sub.status}"
    except AppError as exc:
        msg = f"Error: {exc.message}"
    return redirect(f"/cases/{case.id}", msg)


# ------------------------------------------------------------------------- submissions
@router.get("/submissions", response_class=HTMLResponse)
async def submissions_page(request: Request, status: str | None = None, page: int = 1, db: AsyncSession = Depends(get_db),
                           user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_SUBMISSIONS_READ)):
        return r
    limit = 50
    rows, total = await submission_service.list_submissions(db, status=status or None, limit=limit, offset=(page - 1) * limit)
    return render(request, "submissions.html", user, rows=rows, total=total, page=page, pages=max(1, -(-total // limit)),
                  stats=await submission_service.submission_stats(db), filters={"status": status})


@router.post("/submissions/{submission_id}/{action}")
async def submission_action(request: Request, submission_id: str, action: str, reference_number: str | None = Form(None),
                            note: str | None = Form(None), outcome: str | None = Form(None), body: str | None = Form(None),
                            db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    perm = rbac.P_SUBMISSIONS_EXECUTE if action in ("execute", "confirm") else rbac.P_SUBMISSIONS_WRITE
    if (r := await need(request, user, perm)):
        return r
    sub = await submission_service.get_submission(db, submission_id)
    try:
        if action == "execute":
            await submission_service.execute_submission(db, sub, approved_by=user.username)
            msg = f"Executed → {sub.status}"
        elif action == "confirm":
            await submission_service.confirm_manual_sent(db, sub, reference_number=reference_number or None, actor=user.username, note=note)
            msg = "Confirmed as sent"
        elif action == "response":
            await submission_service.record_response(db, sub, outcome=outcome or "info", body=body, reference_number=reference_number, actor=user.username)
            msg = "Response recorded"
        elif action == "cancel":
            await submission_service.cancel_submission(db, sub, actor=user.username, reason=note)
            msg = "Cancelled"
        else:
            msg = "unknown action"
    except AppError as exc:
        msg = f"Error: {exc.message}"
    return redirect(f"/cases/{sub.case_id}", msg)


# ------------------------------------------------------------------------- errors / audit / reports / settings
@router.get("/errors", response_class=HTMLResponse)
async def errors_page(request: Request, category: str | None = None, db: AsyncSession = Depends(get_db),
                      user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_MONITORING_READ)):
        return r
    q = select(ErrorRecord).order_by(ErrorRecord.occurred_at.desc()).limit(300)
    if category:
        q = q.where(ErrorRecord.category == category)
    return render(request, "errors.html", user, rows=(await db.execute(q)).scalars().all(),
                  causes=await analytics.failure_causes(db, 30), category=category)


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request, action: str | None = None, actor: str | None = None, case_id: str | None = None,
                     page: int = 1, db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_AUDIT_READ)):
        return r
    limit = 100
    rows, total = await search_audit(db, action=action or None, actor=actor or None, case_id=case_id or None,
                                     limit=limit, offset=(page - 1) * limit)
    return render(request, "audit.html", user, rows=rows, total=total, page=page, pages=max(1, -(-total // limit)),
                  actions=[a.value for a in AuditAction], filters={"action": action, "actor": actor, "case_id": case_id})


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_CASES_READ)):
        return r
    return render(request, "reports.html", user, data=await analytics.overview(db),
                  exports=await export_service.list_exports(db, limit=50), types=list(export_service.EXPORTABLE))


@router.post("/reports/export")
async def reports_export(request: Request, export_type: str = Form(...), fmt: str = Form("json"),
                         db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_REPORTS_EXPORT)):
        return r
    try:
        rec = await export_service.export_records(db, export_type, fmt, actor=user.username)
        return redirect("/reports", f"Exported {os.path.basename(rec.file_path)}")
    except AppError as exc:
        return redirect("/reports", f"Error: {exc.message}")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_MONITORING_READ)):
        return r
    from app.backup.manager import BackupManager

    users = (await db.execute(select(User).order_by(User.username))).scalars().all() if rbac.has_permission(user, rbac.P_USERS_MANAGE) else []
    return render(request, "settings.html", user, metrics=await full_snapshot(db), users=users,
                  backups=await BackupManager().list_backups(db), reasons=await reason_service.list_reasons(db),
                  permissions=sorted(rbac.ROLE_PERMISSIONS.get(user.role, [])))


@router.post("/settings/backup/{backup_type}")
async def settings_backup(request: Request, backup_type: str, db: AsyncSession = Depends(get_db),
                          user: User | None = Depends(get_current_user_optional)):
    if (r := await need(request, user, rbac.P_BACKUPS_MANAGE)):
        return r
    from app.backup.manager import BackupManager

    try:
        rec = await BackupManager().backup(db, backup_type, actor=user.username)
        return redirect("/settings", f"Backup created: {os.path.basename(rec.file_path)}")
    except AppError as exc:
        return redirect("/settings", f"Error: {exc.message}")
