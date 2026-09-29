"""Evidence with integrity hashes and chain of custody (§12). Files live under EVIDENCE_ROOT/<case_id>/ and are
never served by path; access goes through this service and is recorded."""
from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.db.models import Case, Evidence, EvidenceCustody
from app.domain.enums import AuditAction, CaseStatus, EvidenceType
from app.services.audit import record_audit
from app.services.errors import NotFoundError, TransitionError, ValidationFailed
from app.utils import sha256_bytes, sha256_file

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_name(name: str) -> str:
    return _SAFE.sub("_", name)[:120] or "file"


def _custody(db: AsyncSession, ev: Evidence, action: str, actor: str | None, sha: str | None, notes: str | None = None):
    c = EvidenceCustody(evidence_id=ev.id, action=action, actor=actor, at=_now(), sha256_at_time=sha, notes=notes)
    db.add(c)
    return c


def _assert_open(case: Case) -> None:
    if case.status in (CaseStatus.CLOSED.value,):
        raise TransitionError("cannot modify evidence of a closed case")


async def add_file_evidence(db: AsyncSession, case: Case, *, evidence_type: str, title: str, data: bytes,
                            original_name: str, mime_type: str | None = None, description: str | None = None,
                            captured_at: datetime | None = None, actor: str | None = None,
                            settings: Settings | None = None) -> Evidence:
    _assert_open(case)
    if evidence_type not in {e.value for e in EvidenceType}:
        raise ValidationFailed("invalid evidence_type")
    if not data:
        raise ValidationFailed("empty file")
    settings = settings or get_settings()
    settings.ensure_dirs()
    ev = Evidence(case_id=case.id, target_id=case.target_id, evidence_type=evidence_type, title=title,
                  description=description, original_name=original_name, mime_type=mime_type, size_bytes=len(data),
                  sha256=sha256_bytes(data), captured_at=captured_at, added_by=actor, integrity_ok=True,
                  last_verified_at=_now())
    db.add(ev)
    await db.flush()
    folder = settings.evidence_root / case.id
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{ev.id}__{_safe_name(original_name)}"
    dest.write_bytes(data)
    dest.chmod(0o600)
    ev.storage_path = str(dest.resolve())
    _custody(db, ev, "added", actor, ev.sha256, f"stored as {dest.name}")
    await record_audit(db, AuditAction.EVIDENCE_ADDED, actor=actor, entity_type="evidence", entity_id=ev.id,
                       case_id=case.id, details={"type": evidence_type, "sha256": ev.sha256, "size": ev.size_bytes})
    return ev


async def add_reference_evidence(db: AsyncSession, case: Case, *, evidence_type: str, title: str,
                                 value: str, description: str | None = None, captured_at: datetime | None = None,
                                 actor: str | None = None) -> Evidence:
    """URL / message reference / hash / timestamp evidence: the value itself is hashed for integrity."""
    _assert_open(case)
    if evidence_type not in {e.value for e in EvidenceType}:
        raise ValidationFailed("invalid evidence_type")
    if not value.strip():
        raise ValidationFailed("value required")
    canonical = f"{evidence_type}:{value.strip()}".encode()
    ev = Evidence(case_id=case.id, target_id=case.target_id, evidence_type=evidence_type, title=title,
                  description=description, external_url=value.strip() if evidence_type in ("url", "message") else None,
                  sha256=value.strip().lower() if evidence_type == "hash" else sha256_bytes(canonical),
                  captured_at=captured_at or _now(), added_by=actor, integrity_ok=True, last_verified_at=_now())
    if evidence_type not in ("url", "message"):
        ev.description = (description or "") + f"\nvalue: {value.strip()}"
    db.add(ev)
    await db.flush()
    _custody(db, ev, "added", actor, ev.sha256)
    await record_audit(db, AuditAction.EVIDENCE_ADDED, actor=actor, entity_type="evidence", entity_id=ev.id,
                       case_id=case.id, details={"type": evidence_type, "sha256": ev.sha256})
    return ev


async def get_evidence(db: AsyncSession, evidence_id: str) -> Evidence:
    ev = (await db.execute(select(Evidence).where(Evidence.id == evidence_id)
                           .options(selectinload(Evidence.custody)))).scalar_one_or_none()
    if not ev:
        raise NotFoundError("evidence not found")
    return ev


async def list_evidence(db: AsyncSession, case_id: str) -> list[Evidence]:
    return list((await db.execute(select(Evidence).where(Evidence.case_id == case_id)
                                  .options(selectinload(Evidence.custody)).order_by(Evidence.created_at))).scalars().all())


async def verify_evidence(db: AsyncSession, ev: Evidence, actor: str | None = None) -> bool:
    ok: bool
    if ev.storage_path:
        p = Path(ev.storage_path)
        ok = p.exists() and sha256_file(p) == ev.sha256
    else:
        ok = bool(ev.sha256)
    ev.integrity_ok = ok
    ev.last_verified_at = _now()
    _custody(db, ev, "verified", actor, ev.sha256, "OK" if ok else "MISMATCH/MISSING")
    await record_audit(db, AuditAction.EVIDENCE_VERIFIED, actor=actor, entity_type="evidence", entity_id=ev.id,
                       case_id=ev.case_id, result="OK" if ok else "FAILED")
    return ok


async def verify_case_evidence(db: AsyncSession, case: Case, actor: str | None = None) -> dict:
    items = await list_evidence(db, case.id)
    results = {ev.id: await verify_evidence(db, ev, actor) for ev in items}
    return {"total": len(items), "ok": sum(results.values()), "failed": [k for k, v in results.items() if not v]}


async def read_evidence_bytes(db: AsyncSession, ev: Evidence, actor: str | None = None) -> bytes:
    if not ev.storage_path:
        raise ValidationFailed("this evidence has no stored file")
    p = Path(ev.storage_path)
    if not p.exists():
        raise NotFoundError("evidence file missing on disk")
    _custody(db, ev, "accessed", actor, ev.sha256)
    return p.read_bytes()


async def build_evidence_package(db: AsyncSession, case: Case, *, actor: str | None = None,
                                 settings: Settings | None = None) -> Path:
    """ZIP with every stored file + manifest.json (hashes, custody chain). Custody 'exported' is recorded."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    items = await list_evidence(db, case.id)
    out = settings.export_root / f"evidence_{case.case_number}_{_now().strftime('%Y%m%dT%H%M%SZ')}.zip"
    manifest = {"case_id": case.id, "case_number": case.case_number, "generated_at": _now().isoformat(),
                "generated_by": actor, "evidence": []}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for ev in items:
            exported = _custody(db, ev, "exported", actor, ev.sha256, out.name)
            chain = list(ev.custody) + [exported]
            entry = {"id": ev.id, "type": ev.evidence_type, "title": ev.title, "sha256": ev.sha256,
                     "external_url": ev.external_url, "captured_at": ev.captured_at.isoformat() if ev.captured_at else None,
                     "added_by": ev.added_by, "integrity_ok": ev.integrity_ok, "file": None,
                     "custody": [{"action": c.action, "actor": c.actor, "at": c.at.isoformat(), "sha256": c.sha256_at_time,
                                  "notes": c.notes} for c in chain]}
            if ev.storage_path and Path(ev.storage_path).exists():
                arc = f"files/{ev.id}__{_safe_name(ev.original_name or 'file')}"
                zf.write(ev.storage_path, arc)
                entry["file"] = arc
            manifest["evidence"].append(entry)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return out
