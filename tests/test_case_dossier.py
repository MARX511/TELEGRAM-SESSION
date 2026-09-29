"""The official case dossier: one tamper-evident ZIP for a government / law-enforcement authority — the case
report (PDF), every evidence file with its hash and chain of custody, the submission log and the full audit
trail, and a MANIFEST that hashes each file so the package can be checked for tampering."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from app.services import cases as cs
from app.services import evidence as ev
from app.services import exports as ex
from app.services.audit import search_audit
from app.services.targets import create_target
from tests.conftest import login


async def _dossier_case(db):
    t = await create_target(db, target_type="channel", url="https://t.me/badchan", title="Bad channel", actor="op")
    c = await cs.create_case(db, target_id=t.id, title="Illegal content", reason_code="illegal_content",
                             explanation="Distributes illegal material.", legal_basis="Art. 5", actor="op")
    await ev.add_reference_evidence(db, c, evidence_type="url", title="post", value="https://t.me/badchan/12", actor="op")
    await ev.add_file_evidence(db, c, evidence_type="screenshot", title="shot", data=b"\x89PNG screenshot bytes",
                               original_name="shot.png", mime_type="image/png", actor="op")
    await cs.generate_draft(db, c, actor="op")
    await cs.submit_for_review(db, c, actor="op")
    await cs.approve(db, c, actor="reviewer")
    await cs.mark_ready(db, c, actor="op")
    return c


async def test_dossier_bundles_report_evidence_submissions_and_audit(db, seeded, settings):
    c = await _dossier_case(db)
    rec = await ex.export_case_dossier(db, await cs.get_case(db, c.id), actor="op")
    assert rec.export_type == "case_dossier" and rec.fmt == "zip"
    p = Path(rec.file_path)
    assert p.exists() and rec.sha256 and rec.size_bytes == p.stat().st_size
    with zipfile.ZipFile(p) as zf:
        names = set(zf.namelist())
        assert {"01_case_report.pdf", "02_case.json", "03_submissions.json", "04_audit_trail.json", "README.txt",
                "evidence/manifest.json", "MANIFEST.json"} <= names
        assert any(n.startswith("evidence/files/") for n in names)
        assert zf.read("01_case_report.pdf").startswith(b"%PDF")
        case_json = json.loads(zf.read("02_case.json"))
        assert case_json["case_number"] == c.case_number and case_json["target"]["url"] == "https://t.me/badchan"
        evman = json.loads(zf.read("evidence/manifest.json"))
        assert len(evman["evidence"]) == 2 and all(e["sha256"] for e in evman["evidence"])
        assert all(any(cst["action"] == "exported" for cst in e["custody"]) for e in evman["evidence"])
        audit = json.loads(zf.read("04_audit_trail.json"))
        assert audit["count"] >= 4 and all(a["case_id"] == c.id for a in audit["audit"])


async def test_dossier_manifest_hashes_match_every_file(db, seeded):
    """MANIFEST.json must let a recipient prove the package is intact: each listed hash matches the bytes."""
    c = await _dossier_case(db)
    rec = await ex.export_case_dossier(db, await cs.get_case(db, c.id), actor="op")
    with zipfile.ZipFile(rec.file_path) as zf:
        man = json.loads(zf.read("MANIFEST.json"))
        assert man["package"] == "official_case_dossier" and man["evidence_count"] == 2
        checked = 0
        for name, meta in man["files"].items():
            if "sha256" in meta and name in zf.namelist():
                assert hashlib.sha256(zf.read(name)).hexdigest() == meta["sha256"], name
                checked += 1
        assert checked >= 4  # pdf + the three json files at least


async def test_dossier_is_recorded_and_audited(db, seeded):
    c = await _dossier_case(db)
    _, before = await search_audit(db, action="Export Created")
    await ex.export_case_dossier(db, await cs.get_case(db, c.id), actor="auditor")
    exports = await ex.list_exports(db)
    assert exports[0].export_type == "case_dossier" and exports[0].requested_by == "auditor"
    _, after = await search_audit(db, action="Export Created")
    assert after == before + 1


async def test_evidence_zip_still_works_after_refactor(db, seeded):
    c = await _dossier_case(db)
    rec = await ex.export_evidence_zip(db, await cs.get_case(db, c.id), actor="op")
    with zipfile.ZipFile(rec.file_path) as zf:
        assert "manifest.json" in zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
    assert len(manifest["evidence"]) == 2


# ----------------------------------------------------------------------------------------------- web + API + RBAC
async def test_case_page_offers_the_dossier_and_downloads_it(client, db, seeded):
    r = await client.post("/login", data={"username": "admin", "password": "admin-pass-123", "next": "/"})
    assert r.status_code == 303
    c = await _dossier_case(db)
    await db.commit()
    html = (await client.get(f"/cases/{c.id}")).text
    assert 'action="/cases/' + c.id + '/action/dossier"' in html and "ملف القضية الرسمي" in html
    # posting the action generates the dossier and redirects straight to its download
    r = await client.post(f"/cases/{c.id}/action/dossier")
    assert r.status_code == 303 and "/exports/" in r.headers["location"] and r.headers["location"].endswith("/download")
    dl = await client.get(r.headers["location"])
    assert dl.status_code == 200 and dl.content[:2] == b"PK"  # a ZIP
    assert "dossier_" in dl.headers.get("content-disposition", "")


async def test_dossier_action_is_permission_gated(client, db, seeded):
    """A viewer (no cases:write) cannot trigger the dossier action at all."""
    c = await _dossier_case(db)
    await db.commit()
    r = await client.post("/login", data={"username": "viewer", "password": "viewer-pass-123", "next": "/"})
    assert r.status_code == 303
    r = await client.post(f"/cases/{c.id}/action/dossier")
    assert r.status_code == 303 and r.headers["location"] == "/" and "/exports/" not in r.headers["location"]
    # the dossier button is not shown to a viewer either
    assert "/action/dossier" not in (await client.get(f"/cases/{c.id}")).text


async def test_api_dossier_endpoint(client, db, seeded):
    op = await login(client, "operator")
    c = await _dossier_case(db)
    await db.commit()
    r = await client.post(f"/api/v1/exports/cases/{c.id}/dossier", headers=op)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["export_type"] == "case_dossier" and body["fmt"] == "zip"
    dl = await client.get(f"/api/v1/exports/{body['id']}/download", headers=op)
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
