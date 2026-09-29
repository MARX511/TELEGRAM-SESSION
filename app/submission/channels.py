"""Official submission channels (§13).

Design rule: a report is filed ONCE per case through an official, documented channel by an authorised operator.
No channel here uses Telegram user sessions/accounts, and no channel fans out a report across accounts.
  - ManualChannel        : operator files via the official portal / in-app report; the platform records it.
  - OfficialPortalChannel: same as manual, plus a ready-to-paste package file and the portal hint.
  - OfficialEmailChannel : builds an email to ONE allow-listed official address, with the case's file evidence
                           attached (integrity-checked, size-capped); SMTP send only when enabled and explicitly
                           approved, otherwise a .eml draft is produced for the operator's mailbox.
  - OfficialApiChannel   : placeholder that refuses: no documented public reporting API is assumed to exist.
"""
from __future__ import annotations

import asyncio
import re
import smtplib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Protocol

from app.config import Settings, get_settings
from app.domain.enums import ExecutionStatus, SubmissionChannel
from app.services.errors import SubmissionError

# Documented official contact points (see docs/CASE_WORKFLOW.md). Data, not behaviour; extend via settings if needed.
OFFICIAL_EMAIL_RECIPIENTS: dict[str, str] = {
    "abuse@telegram.org": "Illegal content / abuse",
    "dmca@telegram.org": "Copyright (DMCA)",
    "stopca@telegram.org": "Child safety",
}
OFFICIAL_PORTALS: dict[str, str] = {
    "in_app_report": "Report button inside the official Telegram apps",
    "telegram_support": "https://telegram.org/support",
}


@dataclass
class ReportPackage:
    case_number: str
    subject: str
    summary: str
    reason: str
    evidence_summary: str
    requested_review: str
    reference: str
    additional_notes: str = ""
    legal_basis: str | None = None
    target: dict = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return asdict(self)

    def as_text(self) -> str:
        parts = [f"Subject: {self.subject}", "", "== Summary ==", self.summary, "", "== Reason ==", self.reason, "",
                 "== Evidence ==", self.evidence_summary, "", "== Requested review ==", self.requested_review, "",
                 "== Reference ==", self.reference]
        if self.additional_notes:
            parts += ["", "== Additional notes ==", self.additional_notes]
        return "\n".join(parts)


@dataclass
class Attachment:
    """A file-evidence item attached to an official email. sha256 is re-verified before it is attached."""

    filename: str
    mime_type: str
    data: bytes
    sha256: str


# Official mailboxes reject oversized mail (commonly 25 MB after base64, which adds about a third).
MAX_EMAIL_ATTACHMENT_BYTES = 15 * 1024 * 1024
_MIME = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+")


def _attachment_name(name: str) -> str:
    """Uploaded names are user input: no control characters or path parts in a MIME header."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(ch for ch in base if ch.isprintable()).strip()[:150] or "evidence"


@dataclass
class SubmitOutcome:
    status: ExecutionStatus
    result: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    reference_number: str | None = None
    artifact_path: str | None = None


class ChannelAdapter(Protocol):
    channel: SubmissionChannel

    async def submit(self, package: ReportPackage, recipient: str | None, *, approved_by: str,
                     attachments: list[Attachment] | None = None) -> SubmitOutcome: ...


def _write_artifact(settings: Settings, package: ReportPackage, suffix: str, content: str | bytes) -> Path:
    settings.ensure_dirs()
    out = settings.export_root / f"submission_{package.case_number}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{suffix}"
    if isinstance(content, str):
        out.write_text(content, encoding="utf-8")
    else:
        out.write_bytes(content)
    out.chmod(0o600)
    return out


class ManualChannel:
    channel = SubmissionChannel.MANUAL

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def submit(self, package: ReportPackage, recipient: str | None, *, approved_by: str,
                     attachments: list[Attachment] | None = None) -> SubmitOutcome:
        path = _write_artifact(self.settings, package, ".txt", package.as_text())
        return SubmitOutcome(status=ExecutionStatus.WAITING, artifact_path=str(path),
                             result="package prepared; operator files it through the official channel and confirms "
                                    "with the reference number")


class OfficialPortalChannel:
    channel = SubmissionChannel.OFFICIAL_PORTAL

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def submit(self, package: ReportPackage, recipient: str | None, *, approved_by: str,
                     attachments: list[Attachment] | None = None) -> SubmitOutcome:
        portal = (recipient or "in_app_report").lower()
        if portal not in OFFICIAL_PORTALS:
            raise SubmissionError(f"unknown official portal '{recipient}'. Known: {', '.join(OFFICIAL_PORTALS)}")
        path = _write_artifact(self.settings, package, ".txt", package.as_text())
        return SubmitOutcome(status=ExecutionStatus.WAITING, artifact_path=str(path),
                             result=f"package prepared for '{portal}' ({OFFICIAL_PORTALS[portal]}); awaiting operator confirmation")


class OfficialEmailChannel:
    channel = SubmissionChannel.OFFICIAL_EMAIL

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def build_message(self, package: ReportPackage, recipient: str,
                      attachments: list[Attachment] | None = None) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = package.subject[:250]
        msg["To"] = recipient
        msg["From"] = self.settings.smtp_from or "legal-reporting@localhost"
        msg["Message-ID"] = make_msgid(domain="tg-legal-platform")
        msg["X-Case-Number"] = package.case_number
        body = package.as_text()
        if attachments:
            body += "\n\n== Attachments ==\n" + "\n".join(f"- {a.filename} (sha256 {a.sha256})" for a in attachments)
        msg.set_content(body)
        for a in attachments or []:
            mime = a.mime_type if _MIME.fullmatch(a.mime_type or "") else "application/octet-stream"
            maintype, _, subtype = mime.partition("/")
            msg.add_attachment(a.data, maintype=maintype, subtype=subtype, filename=_attachment_name(a.filename))
        return msg

    async def submit(self, package: ReportPackage, recipient: str | None, *, approved_by: str,
                     attachments: list[Attachment] | None = None) -> SubmitOutcome:
        if not recipient:
            raise SubmissionError("recipient is required for official email")
        rcpt = recipient.strip().lower()
        if rcpt not in OFFICIAL_EMAIL_RECIPIENTS:
            raise SubmissionError(f"'{recipient}' is not an allow-listed official address: "
                                  f"{', '.join(OFFICIAL_EMAIL_RECIPIENTS)}")
        if not approved_by:
            raise SubmissionError("explicit approval is required before sending")
        msg = self.build_message(package, rcpt, attachments)
        n_att = len(attachments or [])
        s = self.settings
        if not (s.submission_email_enabled and s.smtp_host and s.smtp_from):
            path = _write_artifact(s, package, ".eml", msg.as_bytes())
            return SubmitOutcome(status=ExecutionStatus.WAITING, artifact_path=str(path),
                                 reference_number=msg["Message-ID"],
                                 result=f"SMTP disabled: .eml draft ({n_att} attachments) produced for the operator's "
                                        "mailbox; confirm once sent")

        def _send() -> None:
            implicit_tls = s.smtp_port == 465  # SMTPS; other ports use STARTTLS when the server offers it
            smtp_cls = smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP
            with smtp_cls(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                if not implicit_tls and smtp.has_extn("starttls"):
                    smtp.starttls()
                    smtp.ehlo()
                if s.smtp_user and s.smtp_password:
                    smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)

        try:
            await asyncio.to_thread(_send)
        except (smtplib.SMTPException, OSError) as exc:
            return SubmitOutcome(status=ExecutionStatus.FAILED, error_code="SUBMISSION_ERROR",
                                 error_message=f"{type(exc).__name__}: {exc}")
        path = _write_artifact(s, package, ".eml", msg.as_bytes())
        return SubmitOutcome(status=ExecutionStatus.COMPLETED, reference_number=msg["Message-ID"],
                             artifact_path=str(path), result=f"sent to {rcpt} by {approved_by} ({n_att} attachments)")


class OfficialApiChannel:
    channel = SubmissionChannel.OFFICIAL_API

    async def submit(self, package: ReportPackage, recipient: str | None, *, approved_by: str,
                     attachments: list[Attachment] | None = None) -> SubmitOutcome:
        raise SubmissionError("no documented official reporting API is available; use manual, official_portal or "
                              "official_email")


def get_channel(channel: str | SubmissionChannel, settings: Settings | None = None) -> ChannelAdapter:
    ch = SubmissionChannel(channel)
    if ch == SubmissionChannel.MANUAL:
        return ManualChannel(settings)
    if ch == SubmissionChannel.OFFICIAL_PORTAL:
        return OfficialPortalChannel(settings)
    if ch == SubmissionChannel.OFFICIAL_EMAIL:
        return OfficialEmailChannel(settings)
    return OfficialApiChannel()
