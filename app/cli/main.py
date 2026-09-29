"""Professional CLI (§21). `app <group> <command>`. Shares the service layer with the API and dashboard."""
from __future__ import annotations

import asyncio
import json
from functools import wraps
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import get_settings
from app.db.engine import get_session_factory, session_scope
from app.utils import dumps

cli = typer.Typer(help="Telegram Session & Legal Reporting Platform", no_args_is_help=True, pretty_exceptions_show_locals=False)
console = Console()
sessions_app = typer.Typer(help="Session registry, checks and health", no_args_is_help=True)
proxies_app = typer.Typer(help="Proxy registry", no_args_is_help=True)
groups_app = typer.Typer(help="Session groups", no_args_is_help=True)
targets_app = typer.Typer(help="Target registry", no_args_is_help=True)
cases_app = typer.Typer(help="Case workflow", no_args_is_help=True)
submissions_app = typer.Typer(help="Official submissions", no_args_is_help=True)
reports_app = typer.Typer(help="Exports and analytics", no_args_is_help=True)
audit_app = typer.Typer(help="Audit log", no_args_is_help=True)
users_app = typer.Typer(help="Users / RBAC", no_args_is_help=True)
backups_app = typer.Typer(help="Backup / restore / recovery", no_args_is_help=True)
security_app = typer.Typer(help="Security utilities", no_args_is_help=True)
db_app = typer.Typer(help="Database", no_args_is_help=True)
for name, sub in [("sessions", sessions_app), ("proxies", proxies_app), ("groups", groups_app), ("targets", targets_app),
                  ("cases", cases_app), ("submissions", submissions_app), ("reports", reports_app), ("audit", audit_app),
                  ("users", users_app), ("backups", backups_app), ("security", security_app), ("db", db_app)]:
    cli.add_typer(sub, name=name)

OPERATOR = typer.Option("cli", "--as", help="Operator identity recorded in the audit log")


def run(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        try:
            return asyncio.run(fn(*a, **kw))
        except Exception as exc:  # noqa: BLE001
            from app.services.errors import AppError

            if isinstance(exc, AppError):
                console.print(f"[red]error[/red] ({exc.category.value}): {exc.message}")
                raise typer.Exit(1)
            raise

    return wrapper


def table(title: str, columns: list[str], rows: list[list]) -> None:
    t = Table(title=title, show_lines=False)
    for c in columns:
        t.add_column(c)
    for r in rows:
        t.add_row(*[("" if v is None else str(v)) for v in r])
    console.print(t)


# ============================================================================ sessions
@sessions_app.command("scan")
@run
async def sessions_scan(operator: str = OPERATOR):
    """Discover session files under SESSIONS_ROOT and index them."""
    from app.services.sessions import discover_sessions

    async with session_scope() as db:
        rep = await discover_sessions(db, actor=operator)
    console.print(f"discovered={rep.discovered} new={rep.new} updated={rep.updated} missing={rep.missing}")


@sessions_app.command("list")
@run
async def sessions_list(status: str | None = None, health: str | None = None, location: str | None = None,
                        search: str | None = None, limit: int = 200):
    from app.services.sessions import list_sessions

    async with session_scope() as db:
        rows, total = await list_sessions(db, status=status, health=health, location=location, search=search, limit=limit)
    table(f"Sessions ({total})", ["id", "file", "loc", "status", "health", "user", "checks", "fail", "avail", "last_error"],
          [[s.id[:8], s.file_name, s.location, s.status, s.health, s.username, s.check_count, s.consecutive_failures,
            f"{s.availability:.2f}", (s.last_error or "")[:40]] for s in rows])


@sessions_app.command("check")
@run
async def sessions_check(session_id: list[str] = typer.Argument(None), all_active: bool = typer.Option(False, "--all"),
                         group: str | None = None, concurrency: int | None = None, operator: str = OPERATOR):
    """Validate sessions (bounded concurrency). Respects server-defined waits; never bypasses them."""
    from app.services.groups import get_group, group_session_ids
    from app.services.sessions import check_many, list_sessions

    ids = list(session_id or [])
    async with session_scope() as db:
        if group:
            ids += await group_session_ids(db, await get_group(db, group))
        if all_active or not ids:
            rows, _t = await list_sessions(db, location="active", limit=100000)
            ids += [r.id for r in rows if r.enabled]
    rep = await check_many(get_session_factory(), ids, actor=operator, concurrency=concurrency)
    console.print(dumps(rep.__dict__))


@sessions_app.command("health")
@run
async def sessions_health(session_id: str | None = typer.Argument(None)):
    from app.services.sessions import get_session, health_snapshot, list_sessions, session_stats

    async with session_scope() as db:
        if session_id:
            console.print(dumps(health_snapshot(await get_session(db, session_id))))
            return
        stats = await session_stats(db)
        rows, _t = await list_sessions(db, limit=100000)
    console.print(dumps(stats))
    table("Health", ["file", "status", "health", "last_check", "last_ok", "fails", "avail", "rate_limited_until"],
          [[s.file_name, s.status, s.health, s.last_check, s.last_success_at, s.consecutive_failures, f"{s.availability:.2f}",
            s.rate_limited_until] for s in rows])


@sessions_app.command("move")
@run
async def sessions_move(session_id: str, to: str = typer.Argument(..., help="active|disabled|quarantined"), operator: str = OPERATOR):
    from app.domain.enums import SessionLocation
    from app.services.sessions import disable_session, enable_session, get_session, quarantine_session

    async with session_scope() as db:
        s = await get_session(db, session_id)
        fn = {"active": enable_session, "disabled": disable_session, "quarantined": quarantine_session}[SessionLocation(to).value]
        await fn(db, s, actor=operator, reason="cli")
        console.print(f"{s.file_name} -> {s.location}")


@sessions_app.command("encrypt")
@run
async def sessions_encrypt(session_id: list[str] = typer.Argument(None), all_plain: bool = typer.Option(False, "--all"),
                           operator: str = OPERATOR):
    """Encrypt session files at rest with SESSION_FILE_ENCRYPTION_KEY."""
    from app.security.session_crypto import SessionCrypto
    from app.services.sessions import encrypt_session_file, get_session, list_sessions

    crypto = SessionCrypto(get_settings().session_file_encryption_key)
    if not crypto.enabled:
        console.print("[red]SESSION_FILE_ENCRYPTION_KEY not configured (run: app security keygen)[/red]")
        raise typer.Exit(1)
    async with session_scope() as db:
        ids = list(session_id or [])
        if all_plain:
            rows, _t = await list_sessions(db, limit=100000)
            ids += [r.id for r in rows if not r.encrypted]
        n = 0
        for sid in ids:
            await encrypt_session_file(db, await get_session(db, sid), crypto, actor=operator)
            n += 1
    console.print(f"encrypted {n} file(s)")


# ============================================================================ groups / proxies
@groups_app.command("create")
@run
async def groups_create(name: str, description: str | None = None, proxy_id: str | None = None, operator: str = OPERATOR):
    from app.services.groups import create_group

    async with session_scope() as db:
        g = await create_group(db, name=name, description=description, proxy_id=proxy_id, actor=operator)
        console.print(f"group {g.id} '{g.name}'")


@groups_app.command("list")
@run
async def groups_list():
    from app.services.groups import list_groups

    async with session_scope() as db:
        rows = await list_groups(db)
    table("Groups", ["id", "name", "enabled", "proxy", "concurrency"], [[g.id[:8], g.name, g.enabled, g.proxy_id, g.check_concurrency] for g in rows])


@groups_app.command("assign")
@run
async def groups_assign(group_id: str, session_id: list[str], operator: str = OPERATOR):
    from app.services.groups import assign_sessions, get_group

    async with session_scope() as db:
        n = await assign_sessions(db, await get_group(db, group_id), session_id, actor=operator)
    console.print(f"assigned {n}")


@groups_app.command("enable")
@run
async def groups_enable(group_id: str, enabled: bool = True, operator: str = OPERATOR):
    from app.services.groups import get_group, set_group_enabled

    async with session_scope() as db:
        await set_group_enabled(db, await get_group(db, group_id), enabled, actor=operator)
    console.print("ok")


@proxies_app.command("add")
@run
async def proxies_add(host: str, port: int, protocol: str = "socks5", name: str | None = None, username: str | None = None,
                      secret_ref: str | None = None, operator: str = OPERATOR):
    from app.services.proxies import create_proxy

    async with session_scope() as db:
        p = await create_proxy(db, host=host, port=port, protocol=protocol, name=name, username=username, secret_ref=secret_ref, actor=operator)
        console.print(f"proxy {p.id}")


@proxies_app.command("list")
@run
async def proxies_list():
    from app.services.proxies import list_proxies

    async with session_scope() as db:
        rows = await list_proxies(db)
    table("Proxies", ["id", "name", "endpoint", "status", "latency_ms", "last_check", "enabled"],
          [[p.id[:8], p.name, f"{p.protocol}://{p.host}:{p.port}", p.status, p.latency_ms, p.last_check, p.enabled] for p in rows])


@proxies_app.command("check")
@run
async def proxies_check(proxy_id: list[str] = typer.Argument(None), operator: str = OPERATOR):
    from app.services.proxies import check_proxy, get_proxy, list_proxies

    async with session_scope() as db:
        rows = [await get_proxy(db, i) for i in proxy_id] if proxy_id else await list_proxies(db)
        for p in rows:
            await check_proxy(db, p, actor=operator)
            console.print(f"{p.host}:{p.port} -> {p.status} {p.latency_ms or ''}ms {p.last_error or ''}")


# ============================================================================ targets
@targets_app.command("add")
@run
async def targets_add(target_type: str, url: str | None = None, username: str | None = None, telegram_id: int | None = None,
                      title: str | None = None, description: str | None = None, tags: str | None = None, operator: str = OPERATOR):
    from app.services.targets import create_target

    async with session_scope() as db:
        t = await create_target(db, target_type=target_type, url=url, username=username, telegram_id=telegram_id, title=title,
                                description=description, tags=tags, actor=operator)
        console.print(f"target {t.id}")


@targets_app.command("list")
@run
async def targets_list(target_type: str | None = None, status: str | None = None, limit: int = 200):
    from app.services.targets import list_targets

    async with session_scope() as db:
        rows, total = await list_targets(db, target_type=target_type, status=status, limit=limit)
    table(f"Targets ({total})", ["id", "type", "username", "url", "title", "status"],
          [[t.id[:8], t.target_type, t.username, t.url, t.title, t.status] for t in rows])


@targets_app.command("search")
@run
async def targets_search(query: str):
    from app.services.targets import list_targets

    async with session_scope() as db:
        rows, total = await list_targets(db, search=query)
    table(f"Targets matching '{query}' ({total})", ["id", "type", "username", "url", "title"],
          [[t.id, t.target_type, t.username, t.url, t.title] for t in rows])


# ============================================================================ cases
@cases_app.command("create")
@run
async def cases_create(target_id: str, title: str, reason: str | None = None, explanation: str | None = None,
                       legal_basis: str | None = None, priority: str = "NORMAL", operator: str = OPERATOR):
    from app.services.cases import create_case

    async with session_scope() as db:
        c = await create_case(db, target_id=target_id, title=title, reason_code=reason, explanation=explanation,
                              legal_basis=legal_basis, priority=priority, assigned_operator=operator, actor=operator)
        console.print(f"{c.case_number} ({c.id})")


@cases_app.command("list")
@run
async def cases_list(status: str | None = None, limit: int = 200):
    from app.services.cases import list_cases

    async with session_scope() as db:
        rows, total = await list_cases(db, status=status, limit=limit)
    table(f"Cases ({total})", ["number", "id", "title", "status", "priority", "operator", "reference"],
          [[c.case_number, c.id[:8], c.title[:40], c.status, c.priority, c.assigned_operator, c.reference_number] for c in rows])


@cases_app.command("view")
@run
async def cases_view(case_id: str):
    from app.services.cases import get_case
    from app.services.evidence import list_evidence

    async with session_scope() as db:
        c = await get_case(db, case_id)
        ev = await list_evidence(db, c.id)
        data = {"case_number": c.case_number, "id": c.id, "title": c.title, "status": c.status, "priority": c.priority,
                "target": {"type": c.target.target_type, "url": c.target.url, "username": c.target.username},
                "reason": c.reason.code if c.reason else None, "legal_basis": c.legal_basis, "reference": c.reference_number,
                "approved_by": c.approved_by, "draft": json.loads(c.draft_json) if c.draft_json else None,
                "evidence": [{"type": e.evidence_type, "title": e.title, "sha256": e.sha256, "ok": e.integrity_ok} for e in ev],
                "history": [{"at": e.created_at.isoformat(), "event": e.event_type, "actor": e.actor, "to": e.to_status} for e in c.events]}
    console.print_json(dumps(data))


@cases_app.command("reason")
@run
async def cases_reason(case_id: str, reason_code: str, operator: str = OPERATOR):
    from app.services.cases import get_case, set_reason

    async with session_scope() as db:
        await set_reason(db, await get_case(db, case_id), reason_code, actor=operator)
    console.print("ok")


@cases_app.command("draft")
@run
async def cases_draft(case_id: str, template_id: str | None = None, operator: str = OPERATOR):
    from app.services.cases import generate_draft, get_case

    async with session_scope() as db:
        pkg = await generate_draft(db, await get_case(db, case_id), template_id=template_id, actor=operator)
    console.print_json(dumps(pkg))


@cases_app.command("transition")
@run
async def cases_transition(case_id: str, to_status: str, note: str | None = None, operator: str = OPERATOR):
    from app.services.cases import get_case, transition

    async with session_scope() as db:
        c = await transition(db, await get_case(db, case_id), to_status, actor=operator, note=note)
    console.print(f"{c.case_number} -> {c.status}")


@cases_app.command("approve")
@run
async def cases_approve(case_id: str, note: str | None = None, operator: str = OPERATOR):
    """Approve a reviewed draft (reviewer/admin action) and mark the case Ready for submission."""
    from app.services.cases import approve, get_case, mark_ready

    async with session_scope() as db:
        c = await get_case(db, case_id)
        if c.status == "Draft":
            from app.services.cases import submit_for_review

            await submit_for_review(db, c, actor=operator)
        await approve(db, c, actor=operator, note=note)
        await mark_ready(db, c, actor=operator)
    console.print(f"{c.case_number} -> {c.status}")


@cases_app.command("evidence-add")
@run
async def cases_evidence_add(case_id: str, evidence_type: str, title: str, value: str | None = None,
                             file: Path | None = None, description: str | None = None, operator: str = OPERATOR):
    from app.services.cases import get_case
    from app.services.evidence import add_file_evidence, add_reference_evidence

    async with session_scope() as db:
        c = await get_case(db, case_id)
        if file:
            ev = await add_file_evidence(db, c, evidence_type=evidence_type, title=title, data=file.read_bytes(),
                                         original_name=file.name, description=description, actor=operator)
        else:
            ev = await add_reference_evidence(db, c, evidence_type=evidence_type, title=title, value=value or "",
                                              description=description, actor=operator)
        console.print(f"evidence {ev.id} sha256={ev.sha256}")


@cases_app.command("evidence-verify")
@run
async def cases_evidence_verify(case_id: str, operator: str = OPERATOR):
    from app.services.cases import get_case
    from app.services.evidence import verify_case_evidence

    async with session_scope() as db:
        console.print(dumps(await verify_case_evidence(db, await get_case(db, case_id), actor=operator)))


# ============================================================================ submissions
@submissions_app.command("create")
@run
async def submissions_create(case_id: str, channel: str = "manual", recipient: str | None = None,
                             execute: bool = typer.Option(False, "--execute", help="Run the channel adapter now (explicit approval)"),
                             operator: str = OPERATOR):
    from app.services.cases import get_case
    from app.services.submissions import create_submission, execute_submission

    async with session_scope() as db:
        sub = await create_submission(db, await get_case(db, case_id), channel=channel, recipient=recipient, actor=operator)
        if execute:
            await execute_submission(db, sub, approved_by=operator)
        console.print(f"submission {sub.id} status={sub.status} {sub.result or ''}")


@submissions_app.command("execute")
@run
async def submissions_execute(submission_id: str, operator: str = OPERATOR):
    from app.services.submissions import execute_submission, get_submission

    async with session_scope() as db:
        sub = await execute_submission(db, await get_submission(db, submission_id), approved_by=operator)
        console.print(f"status={sub.status} ref={sub.reference_number} {sub.result or ''} {sub.error_message or ''}")


@submissions_app.command("confirm")
@run
async def submissions_confirm(submission_id: str, reference: str | None = None, note: str | None = None, operator: str = OPERATOR):
    from app.services.submissions import confirm_manual_sent, get_submission

    async with session_scope() as db:
        sub = await confirm_manual_sent(db, await get_submission(db, submission_id), reference_number=reference, actor=operator, note=note)
        console.print(f"status={sub.status}")


@submissions_app.command("respond")
@run
async def submissions_respond(submission_id: str, outcome: str, body: str | None = None, reference: str | None = None, operator: str = OPERATOR):
    from app.services.submissions import get_submission, record_response

    async with session_scope() as db:
        r = await record_response(db, await get_submission(db, submission_id), outcome=outcome, body=body, reference_number=reference, actor=operator)
        console.print(f"response {r.id} outcome={r.outcome}")


@submissions_app.command("status")
@run
async def submissions_status(submission_id: str | None = typer.Argument(None), status: str | None = None):
    from app.services.submissions import get_submission, list_submissions, submission_stats

    async with session_scope() as db:
        if submission_id:
            s = await get_submission(db, submission_id)
            console.print_json(dumps({"id": s.id, "case_id": s.case_id, "channel": s.channel, "recipient": s.recipient,
                                      "status": s.status, "reference": s.reference_number, "result": s.result,
                                      "error": s.error_message, "attempts": [{"no": a.attempt_no, "status": a.status,
                                      "result": a.result, "error": a.error_message} for a in s.attempts],
                                      "responses": [{"outcome": r.outcome, "at": r.received_at.isoformat()} for r in s.responses]}))
            return
        console.print(dumps(await submission_stats(db)))
        rows, total = await list_submissions(db, status=status)
    table(f"Submissions ({total})", ["id", "case", "channel", "recipient", "status", "reference", "result"],
          [[s.id[:8], s.case_id[:8], s.channel, s.recipient, s.status, s.reference_number, (s.result or "")[:50]] for s in rows])


# ============================================================================ reports / audit
@reports_app.command("export")
@run
async def reports_export(export_type: str, fmt: str = "json", operator: str = OPERATOR):
    from app.services.exports import export_records

    async with session_scope() as db:
        rec = await export_records(db, export_type, fmt, actor=operator)
    console.print(f"{rec.file_path} sha256={rec.sha256} bytes={rec.size_bytes}")


@reports_app.command("case-pdf")
@run
async def reports_case_pdf(case_id: str, operator: str = OPERATOR):
    from app.services.cases import get_case
    from app.services.exports import export_case_pdf

    async with session_scope() as db:
        rec = await export_case_pdf(db, await get_case(db, case_id), actor=operator)
    console.print(rec.file_path)


@reports_app.command("evidence-zip")
@run
async def reports_evidence_zip(case_id: str, operator: str = OPERATOR):
    from app.services.cases import get_case
    from app.services.exports import export_evidence_zip

    async with session_scope() as db:
        rec = await export_evidence_zip(db, await get_case(db, case_id), actor=operator)
    console.print(rec.file_path)


@reports_app.command("analytics")
@run
async def reports_analytics():
    from app.services.analytics import overview

    async with session_scope() as db:
        console.print_json(dumps(await overview(db)))


@audit_app.command("search")
@run
async def audit_search(action: str | None = None, actor: str | None = None, case_id: str | None = None,
                       session_id: str | None = None, limit: int = 100):
    from app.services.audit import search_audit

    async with session_scope() as db:
        rows, total = await search_audit(db, action=action, actor=actor, case_id=case_id, session_id=session_id, limit=limit)
    table(f"Audit ({total})", ["at", "action", "actor", "entity", "result", "reason"],
          [[r.at.strftime("%Y-%m-%d %H:%M:%S"), r.action, r.actor, f"{r.entity_type or ''}:{(r.entity_id or '')[:8]}", r.result,
            (r.reason or "")[:40]] for r in rows])


# ============================================================================ users / security / backups / db
@users_app.command("create")
@run
async def users_create(username: str, password: str = typer.Option(..., prompt=True, hide_input=True), role: str = "viewer",
                       full_name: str | None = None, operator: str = OPERATOR):
    from sqlalchemy import select

    from app.db.models import User
    from app.domain.enums import AuditAction
    from app.security.auth import hash_password
    from app.services.audit import record_audit

    async with session_scope() as db:
        if (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
            console.print("[red]username taken[/red]")
            raise typer.Exit(1)
        u = User(username=username, password_hash=hash_password(password), role=role, full_name=full_name)
        db.add(u)
        await db.flush()
        await record_audit(db, AuditAction.USER_CREATED, actor=operator, entity_type="user", entity_id=u.id, details={"role": role})
        console.print(f"user {u.id} role={role}")


@users_app.command("list")
@run
async def users_list():
    from sqlalchemy import select

    from app.db.models import User

    async with session_scope() as db:
        rows = (await db.execute(select(User).order_by(User.username))).scalars().all()
    table("Users", ["id", "username", "role", "active", "last_login"], [[u.id[:8], u.username, u.role, u.is_active, u.last_login_at] for u in rows])


@security_app.command("keygen")
def security_keygen():
    """Generate a SESSION_FILE_ENCRYPTION_KEY value."""
    from app.security.session_crypto import generate_key

    console.print(generate_key())


@backups_app.command("create")
@run
async def backups_create(backup_type: str = typer.Argument("database", help="database|sessions|config|evidence"), operator: str = OPERATOR):
    from app.backup.manager import BackupManager

    async with session_scope() as db:
        rec = await BackupManager().backup(db, backup_type, actor=operator)
    console.print(f"{rec.id} {rec.file_path} sha256={rec.sha256}")


@backups_app.command("list")
@run
async def backups_list():
    from app.backup.manager import BackupManager

    async with session_scope() as db:
        rows = await BackupManager().list_backups(db)
    table("Backups", ["id", "type", "file", "size", "created", "restored"], [[b.id[:8], b.backup_type, b.file_path, b.size_bytes, b.created_at, b.restored_at] for b in rows])


@backups_app.command("verify")
@run
async def backups_verify(backup_id: str):
    from app.backup.manager import BackupManager

    async with session_scope() as db:
        console.print("OK" if await BackupManager().verify(db, backup_id) else "CORRUPT")


@backups_app.command("restore")
@run
async def backups_restore(backup_id: str, yes: bool = typer.Option(False, "--yes"), operator: str = OPERATOR):
    from app.backup.manager import BackupManager

    if not yes:
        typer.confirm("Restore overwrites current data. Continue?", abort=True)
    async with session_scope() as db:
        rec = await BackupManager().restore(db, backup_id, actor=operator)
    console.print(f"restored {rec.backup_type} from {rec.file_path}")


@backups_app.command("integrity")
@run
async def backups_integrity():
    from app.backup.manager import BackupManager

    async with session_scope() as db:
        console.print_json(dumps(await BackupManager().integrity_check(db)))


@backups_app.command("recover")
@run
async def backups_recover(operator: str = OPERATOR):
    """Crash recovery: reconcile session files with the registry and resume pending jobs."""
    from app.backup.manager import recover

    async with session_scope() as db:
        console.print_json(dumps(await recover(db, actor=operator)))


@db_app.command("upgrade")
def db_upgrade():
    import subprocess
    import sys

    raise typer.Exit(subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"]))


@db_app.command("seed")
@run
async def db_seed(operator: str = OPERATOR):
    from app.services.reasons import seed_reasons
    from app.services.templates import seed_templates

    async with session_scope() as db:
        console.print(f"reasons={await seed_reasons(db, actor=operator)} templates={await seed_templates(db, actor=operator)}")


@cli.command("serve")
def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """Run the web dashboard + API."""
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


@cli.command("worker")
@run
async def worker(concurrency: int | None = None):
    """Run a standalone worker pool (for deployments where the API process does not run workers)."""
    from app.workers.handlers import HANDLERS
    from app.workers.queue import JobQueue
    from app.workers.worker import WorkerPool

    pool = WorkerPool(JobQueue(get_session_factory()), HANDLERS, concurrency=concurrency, name="cli-worker")
    await pool.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await pool.stop()


if __name__ == "__main__":
    cli()
