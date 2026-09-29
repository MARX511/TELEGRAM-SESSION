from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.services import cases as cs
from app.services import evidence as ev
from app.services import exports as ex
from app.services.audit import search_audit
from app.services.targets import create_target
from app.utils import sha256_bytes


async def _case(db):
    t = await create_target(db, target_type="account", username="evil", actor="op")
    return await cs.create_case(db, target_id=t.id, title="Evidence case", reason_code="fraud_scam", actor="op")


async def test_file_evidence_integrity_and_custody(db, seeded, settings):
    c = await _case(db)
    data = b"PNG-fake-screenshot-bytes" * 100
    e = await ev.add_file_evidence(db, c, evidence_type="screenshot", title="shot", data=data, original_name="shot.png",
                                   mime_type="image/png", actor="op")
    assert e.sha256 == sha256_bytes(data) and Path(e.storage_path).exists() and e.size_bytes == len(data)
    assert e.storage_path.startswith(str(settings.evidence_root.resolve()))
    assert await ev.verify_evidence(db, e, actor="auditor") is True
    Path(e.storage_path).write_bytes(b"tampered")
    assert await ev.verify_evidence(db, e, actor="auditor") is False
    full = await ev.get_evidence(db, e.id)
    actions = [c_.action for c_ in full.custody]
    assert actions == ["added", "verified", "verified"] and full.custody[-1].notes == "MISMATCH/MISSING"
    assert full.integrity_ok is False
    res = await ev.verify_case_evidence(db, c)
    assert res["total"] == 1 and res["ok"] == 0


async def test_reference_evidence_and_zip_package(db, seeded, settings):
    c = await _case(db)
    await ev.add_reference_evidence(db, c, evidence_type="url", title="post", value="https://t.me/evil/1", actor="op")
    h = "a" * 64
    await ev.add_reference_evidence(db, c, evidence_type="hash", title="video hash", value=h, actor="op")
    await ev.add_file_evidence(db, c, evidence_type="document", title="doc", data=b"%PDF-1.4 fake", original_name="x.pdf", actor="op")
    rec = await ex.export_evidence_zip(db, c, actor="op")
    with zipfile.ZipFile(rec.file_path) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
    assert "manifest.json" in names and any(n.startswith("files/") for n in names)
    assert len(manifest["evidence"]) == 3 and all(x["sha256"] for x in manifest["evidence"])
    assert any(x["sha256"] == h for x in manifest["evidence"])
    assert all(any(cst["action"] == "exported" for cst in x["custody"]) for x in manifest["evidence"])


async def test_json_csv_pdf_exports_are_recorded_and_audited(db, seeded, settings):
    c = await _case(db)
    await cs.generate_draft(db, c, actor="op")
    j = await ex.export_records(db, "cases", "json", actor="auditor")
    csvr = await ex.export_records(db, "cases", "csv", actor="auditor")
    pdf = await ex.export_case_pdf(db, await cs.get_case(db, c.id), actor="auditor")
    for r in (j, csvr, pdf):
        p = Path(r.file_path)
        assert p.exists() and r.sha256 and r.size_bytes == p.stat().st_size
    assert json.loads(Path(j.file_path).read_text())["count"] == 1
    assert "case_number" in Path(csvr.file_path).read_text().splitlines()[0]
    assert Path(pdf.file_path).read_bytes().startswith(b"%PDF")
    sessions_export = await ex.export_records(db, "sessions", "json")
    assert "file_path" not in Path(sessions_export.file_path).read_text()  # absolute paths never leave the system
    rows, total = await search_audit(db, action="Export Created")
    assert total == 4
