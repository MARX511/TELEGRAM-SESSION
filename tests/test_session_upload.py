"""Uploading session files from the dashboard and the API: validation, de-duplication, ZIP safety, encryption at
rest, permissions, and the check-mode (validation provider) status shown next to the upload box."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.security.session_crypto import generate_key
from app.services import sessions as svc
from app.telegram.session_files import create_synthetic_session_file
from app.telegram.validator import provider_status
from tests.conftest import login


def _session(tmp_path: Path, dc_id: int, fmt: str = "telethon") -> bytes:
    """Distinct, structurally valid session databases (no credentials); dc_id changes the content."""
    return create_synthetic_session_file(tmp_path / f"src_{fmt}_{dc_id}.session", fmt=fmt, dc_id=dc_id,
                                         user_id=1000 + dc_id).read_bytes()


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


async def _web_login(client, role="admin"):
    r = await client.post("/login", data={"username": role, "password": f"{role}-pass-123", "next": "/"})
    assert r.status_code == 303


def _active(settings) -> set[str]:
    return {p.name for p in (settings.sessions_root / "active").iterdir()}


def _no_staging_left(settings) -> bool:
    incoming = settings.sessions_root / ".incoming"
    return not incoming.exists() or not any(incoming.iterdir())


# ----------------------------------------------------------------------------------------------- web upload
async def test_upload_registers_sessions_and_queues_checks(client, settings, tmp_path):
    await _web_login(client)
    files = [("files", ("acc_one.session", _session(tmp_path, 1))),
             ("files", ("acc two.session", _session(tmp_path, 2, fmt="pyrogram")))]
    r = await client.post("/sessions/upload", files=files, data={"check": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/sessions"
    page = (await client.get("/sessions")).text
    assert "انتهى الرفع: أُضيفت 2" in page  # the summary is rendered in Arabic (the default language)
    assert _active(settings) == {"acc_one.session", "acc_two.session"}
    api = (await client.get("/api/v1/sessions")).json()
    assert api["total"] == 2 and {i["file_format"] for i in api["items"]} == {"telethon", "pyrogram"}
    assert (await client.get("/api/v1/jobs/stats")).json()["PENDING"] == 2
    audit = (await client.get("/api/v1/audit", params={"action": "Session Uploaded"})).json()
    assert audit["total"] == 1
    assert _no_staging_left(settings)


async def test_duplicates_invalid_files_and_wrong_types_are_reported(client, settings, tmp_path):
    await _web_login(client)
    data = _session(tmp_path, 3)
    assert (await client.post("/sessions/upload", files=[("files", ("a.session", data))])).status_code == 303
    files = [("files", ("same-content-new-name.session", data)),       # duplicate by content
             ("files", ("junk.session", b"this is not an sqlite database at all")),
             ("files", ("notes.txt", b"hello"))]
    r = await client.post("/sessions/upload", files=files)
    assert r.status_code == 303
    page = (await client.get("/sessions")).text
    assert "أُضيفت 0، و1 مسجّلة من قبل، ورُفض 2" in page
    assert "ليس ملف جلسة Telethon أو Pyrogram" in page and "notes.txt" in page
    assert _active(settings) == {"a.session"}
    assert (await client.get("/api/v1/sessions")).json()["total"] == 1
    assert _no_staging_left(settings)


async def test_same_name_different_content_gets_a_new_name(client, settings, tmp_path):
    await _web_login(client)
    await client.post("/sessions/upload", files=[("files", ("acc.session", _session(tmp_path, 1)))])
    await client.post("/sessions/upload", files=[("files", ("acc.session", _session(tmp_path, 2)))])
    assert _active(settings) == {"acc.session", "acc_2.session"}


async def test_zip_upload_extracts_only_session_files_safely(client, settings, tmp_path):
    await _web_login(client)
    archive = _zip({
        "../../escape.session": _session(tmp_path, 1),                 # traversal: kept by basename only
        "nested/dir/inner.session": _session(tmp_path, 2),
        "__MACOSX/nested/._inner.session": b"resource fork",
        ".hidden.session": _session(tmp_path, 3),
        "readme.txt": b"not a session",
        "bad.session": b"garbage",
    })
    r = await client.post("/sessions/upload", files=[("files", ("accounts.zip", archive))])
    assert r.status_code == 303
    assert _active(settings) == {"escape.session", "inner.session"}
    root = settings.sessions_root.resolve()
    assert not any(p.name == "escape.session" and root not in p.resolve().parents
                   for p in settings.sessions_root.parent.rglob("escape.session"))
    page = (await client.get("/sessions")).text
    assert "أُضيفت 2" in page and "accounts.zip/bad.session" in page
    assert _no_staging_left(settings)


async def test_corrupt_empty_and_oversized_archives_are_rejected(client, settings, tmp_path, monkeypatch):
    await _web_login(client)
    r = await client.post("/sessions/upload", files=[("files", ("broken.zip", b"PK\x03\x04 not really a zip"))])
    assert r.status_code == 303 and "ملف ZIP تالف" in (await client.get("/sessions")).text
    await client.post("/sessions/upload", files=[("files", ("empty.zip", _zip({"readme.txt": b"x"})))])
    assert "لا يحتوي على ملفات" in (await client.get("/sessions")).text
    monkeypatch.setattr(svc, "MAX_SESSION_FILE_BYTES", 1024)          # smaller than any session database
    await client.post("/sessions/upload", files=[("files", ("big.zip", _zip({"big.session": _session(tmp_path, 4)}))),
                                                 ("files", ("big2.session", _session(tmp_path, 5)))])
    page = (await client.get("/sessions")).text
    assert "big.zip/big.session (الملف كبير جدًا)" in page and "big2.session (الملف كبير جدًا)" in page
    assert _active(settings) == set() and _no_staging_left(settings)


async def test_zip_total_extraction_is_capped(client, settings, tmp_path, monkeypatch):
    await _web_login(client)
    one = _session(tmp_path, 1)
    monkeypatch.setattr(svc, "MAX_EXTRACTED_BYTES", len(one) + 10)   # room for exactly one member
    archive = _zip({"a.session": one, "b.session": _session(tmp_path, 2)})
    await client.post("/sessions/upload", files=[("files", ("two.zip", archive))])
    assert _active(settings) == {"a.session"}
    assert "two.zip/b.session (الملف كبير جدًا)" in (await client.get("/sessions")).text


async def test_upload_encrypts_at_rest_when_a_key_is_configured(client, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "session_file_encryption_key", generate_key())
    await _web_login(client)
    data = _session(tmp_path, 6)
    await client.post("/sessions/upload", files=[("files", ("secret.session", data))])
    assert _active(settings) == {"secret.session.enc"}
    item = (await client.get("/api/v1/sessions")).json()["items"][0]
    assert item["encrypted"] is True
    assert (settings.sessions_root / "active" / "secret.session.enc").read_bytes() != data
    await client.post("/sessions/upload", files=[("files", ("again.session", data))])   # same plain content
    assert "و1 مسجّلة من قبل" in (await client.get("/sessions")).text
    assert _active(settings) == {"secret.session.enc"}


async def test_upload_requires_the_sessions_write_permission(client, settings, tmp_path):
    await _web_login(client, "viewer")
    r = await client.post("/sessions/upload", files=[("files", ("x.session", _session(tmp_path, 1)))])
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert _active(settings) == set()
    viewer = await login(client, "viewer")
    r = await client.post("/api/v1/sessions/upload", files=[("files", ("x.session", _session(tmp_path, 1)))],
                          headers=viewer)
    assert r.status_code == 403


async def test_api_upload_returns_a_report(client, settings, tmp_path):
    op = await login(client, "operator")
    r = await client.post("/api/v1/sessions/upload", headers=op, data={"check": "true"},
                          files=[("files", ("api.session", _session(tmp_path, 7))),
                                 ("files", ("x.exe", b"MZ"))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == ["api.session"] and body["checks_queued"] == 1 and len(body["session_ids"]) == 1
    assert body["rejected"] == [{"file": "x.exe", "reason": svc.REJECT_TYPE}]


async def test_empty_upload_form_is_an_error(client):
    await _web_login(client)
    r = await client.post("/sessions/upload", data={"check": "1"})
    assert r.status_code == 303
    assert "اختر ملفات" in (await client.get("/sessions")).text


@pytest.mark.parametrize("raw,expected", [
    ("acc.session", "acc.session"), ("../../etc/passwd", "passwd.session"), ("C:\\Users\\me\\x.SESSION", "x.session"),
    ("my account (1).session", "my_account_1.session"), ("..session", "session.session"), ("", "session.session"),
    ("حساب.session", "حساب.session"), ("CON.session", "_CON.session"), ("lpt1.session", "_lpt1.session"),
])
def test_safe_session_name(raw, expected):
    assert svc.safe_session_name(raw) == expected


def test_every_rejection_reason_is_translated():
    from app.web.i18n import AR

    assert all(r in AR for r in svc.REJECT_REASONS)


# ----------------------------------------------------------------------------------------------- check mode
def test_provider_status_explains_what_blocks_real_checks(settings):
    sim = provider_status(settings.model_copy(update={"telegram_provider": "simulation", "telegram_api_id": None,
                                                      "telegram_api_hash": None}))
    assert sim == {"provider": "simulation", "real": False, "ready": False, "has_credentials": False, "problems": []}
    forgot = provider_status(settings.model_copy(update={"telegram_provider": "simulation", "telegram_api_id": 1,
                                                         "telegram_api_hash": "h"}))
    assert forgot["problems"] == ["credentials_set_but_simulation"]
    real = provider_status(settings.model_copy(update={"telegram_provider": "telethon", "telegram_api_id": None,
                                                       "telegram_api_hash": None}))
    assert real["real"] and not real["ready"] and "credentials_missing" in real["problems"]


async def test_sessions_page_shows_upload_box_and_check_mode(client):
    await _web_login(client)
    html = (await client.get("/sessions")).text
    assert 'action="/sessions/upload"' in html and 'enctype="multipart/form-data"' in html
    assert "وضع الفحص" in html and "محاكاة" in html
    await _web_login(client, "viewer")
    html = (await client.get("/sessions")).text
    assert 'action="/sessions/upload"' not in html and "وضع الفحص" in html


# ----------------------------------------------------------------------------------------------- launcher
def test_launcher_reinstalls_only_when_dependencies_change(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("launcher", Path(__file__).parent.parent / "scripts" / "setup_and_run.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    venv_py = tmp_path / "python"
    venv_py.write_text("")
    monkeypatch.setattr(launcher, "VENV_PY", venv_py)
    monkeypatch.setattr(launcher, "INSTALL_STAMP", tmp_path / ".stamp")
    calls: list[list[str]] = []
    monkeypatch.setattr(launcher, "run", lambda cmd, check=True: calls.append(cmd) or 0)
    launcher.ensure_venv()
    assert [c for c in calls if "-e" in c] == [[str(venv_py), "-m", "pip", "install", "-e", ".[telegram]"]]
    calls.clear()
    launcher.ensure_venv()
    assert calls == []                                   # stamp matches: nothing re-installed
    (tmp_path / ".stamp").write_text("an older version")
    launcher.ensure_venv()
    assert any("-e" in c for c in calls)
