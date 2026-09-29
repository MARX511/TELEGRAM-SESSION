"""Official email: file evidence travels as integrity-checked attachments (size-capped), SMTPS on port 465, and
the dashboard no longer offers the refusing 'official API' channel."""
from __future__ import annotations

import email
import re
import smtplib
from pathlib import Path

from sqlalchemy import select

from app.db.models import EvidenceCustody
from app.domain.enums import ExecutionStatus
from app.services import evidence as ev
from app.services import submissions as ss
from app.submission import channels
from app.submission.channels import Attachment, OfficialEmailChannel, ReportPackage
from tests.test_case_workflow import _ready_case

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _package() -> ReportPackage:
    return ReportPackage(case_number="CASE-1", subject="Report CASE-1", summary="s", reason="r", evidence_summary="e",
                         requested_review="q", reference="ref")


async def _email_case(db, settings):
    c = await _ready_case(db)
    await ev.add_file_evidence(db, c, evidence_type="screenshot", title="post", data=PNG, original_name="post.png",
                               mime_type="image/png", actor="op")
    await ev.add_file_evidence(db, c, evidence_type="file", title="log", data=b"chat log", original_name="log.txt",
                               mime_type="text/plain", actor="op")
    return c


async def test_eml_draft_carries_the_file_evidence(db, seeded, settings):
    c = await _email_case(db, settings)
    sub = await ss.create_submission(db, c, channel="official_email", recipient="abuse@telegram.org", actor="op")
    await ss.execute_submission(db, sub, approved_by="reviewer")
    assert sub.status == ExecutionStatus.WAITING.value and "2 attachments" in sub.result
    # the artifact path is recorded on the submission (case numbers restart per test, so do not glob for it)
    eml = Path(re.search(r"\[artifact: ([^\]]+)\]", sub.result).group(1))
    msg = email.message_from_bytes(eml.read_bytes())
    names = {p.get_filename(): p.get_payload(decode=True) for p in msg.walk() if p.get_filename()}
    assert names == {"post.png": PNG, "log.txt": b"chat log"}
    body = next(p for p in msg.walk() if p.get_content_type() == "text/plain" and not p.get_filename())
    assert "== Attachments ==" in body.get_payload(decode=True).decode()
    notes = (await db.execute(select(EvidenceCustody.notes).where(EvidenceCustody.action == "exported"))).scalars().all()
    assert len(notes) == 2 and all(n == f"attached to official email {sub.id}" for n in notes)


async def test_tampered_or_oversized_evidence_is_not_attached(db, seeded, settings, monkeypatch):
    c = await _email_case(db, settings)
    items = await ev.list_evidence(db, c.id)
    tampered = next(e for e in items if e.original_name == "log.txt")
    with open(tampered.storage_path, "ab") as f:
        f.write(b" edited")
    monkeypatch.setattr(ss, "MAX_EMAIL_ATTACHMENT_BYTES", 20)  # fits the edited log (15 B), not the PNG (72 B)
    sub = await ss.create_submission(db, c, channel="official_email", recipient="abuse@telegram.org", actor="op")
    await ss.execute_submission(db, sub, approved_by="reviewer")
    assert "0 attachments" in sub.result
    assert "log.txt (integrity)" in sub.result and "post.png (size)" in sub.result
    assert tampered.integrity_ok is False


def test_build_message_lists_and_attaches_files(settings):
    ch = OfficialEmailChannel(settings)
    att = [Attachment(filename="a.png", mime_type="image/png", data=PNG, sha256="ab" * 32)]
    msg = ch.build_message(_package(), "abuse@telegram.org", att)
    parts = [p for p in msg.iter_attachments()]
    assert [p.get_filename() for p in parts] == ["a.png"] and parts[0].get_content_type() == "image/png"
    assert "a.png (sha256 " + "ab" * 32 + ")" in msg.get_body(("plain",)).get_content()
    assert not list(ch.build_message(_package(), "abuse@telegram.org").iter_attachments())


def test_attachment_metadata_from_uploads_is_sanitised(settings):
    ch = OfficialEmailChannel(settings)
    att = [Attachment(filename="..\\dir/evil\r\nBcc: x@y.z.png", mime_type="text/html; charset=utf-8\r\nX: y",
                      data=b"x", sha256="00" * 32)]
    part = next(ch.build_message(_package(), "abuse@telegram.org", att).iter_attachments())
    assert part.get_filename() == "evil" + "Bcc: x@y.z.png" and part.get_content_type() == "application/octet-stream"
    assert "Bcc" not in str(ch.build_message(_package(), "abuse@telegram.org", att)["Bcc"] or "")


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.tls, self.sent = host, port, False, []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return name == "starttls"

    def starttls(self):
        self.tls = True

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        self.sent.append(msg)


class _FakeSMTPSSL(_FakeSMTP):
    pass


async def _send_with_port(settings, monkeypatch, port):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTPSSL)
    s = settings.model_copy(update={"submission_email_enabled": True, "smtp_host": "smtp.example.org", "smtp_port": port,
                                    "smtp_from": "me@example.org", "smtp_user": "me@example.org",
                                    "smtp_password": "app-password"})
    out = await channels.OfficialEmailChannel(s).submit(_package(), "abuse@telegram.org", approved_by="reviewer",
                                                        attachments=[])
    return out, _FakeSMTP.instances[-1]


async def test_port_465_uses_implicit_tls_and_587_uses_starttls(settings, monkeypatch):
    out, smtp = await _send_with_port(settings, monkeypatch, 465)
    assert out.status == ExecutionStatus.COMPLETED and isinstance(smtp, _FakeSMTPSSL) and smtp.tls is False
    assert smtp.sent and smtp.sent[0]["To"] == "abuse@telegram.org"
    out, smtp = await _send_with_port(settings, monkeypatch, 587)
    assert out.status == ExecutionStatus.COMPLETED and type(smtp) is _FakeSMTP and smtp.tls is True


async def test_case_page_offers_only_working_channels_and_guidance(client, db):
    r = await client.post("/login", data={"username": "admin", "password": "admin-pass-123", "next": "/"})
    assert r.status_code == 303
    c = await _ready_case(db)
    await db.commit()
    html = (await client.get(f"/cases/{c.id}")).text
    assert 'value="official_email"' in html and 'value="official_api"' not in html
    assert "البريد غير مضبوط بعد" in html
