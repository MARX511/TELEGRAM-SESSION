"""Domain enumerations (spec §2, §3, §14, §16, §25). Kept in one place so the DB, API and UI agree."""
from __future__ import annotations

import enum


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    VALID = "VALID"
    BANNED = "BANNED"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    UNAVAILABLE = "UNAVAILABLE"
    CHECK_FAILED = "CHECK_FAILED"
    UNCHECKED = "UNCHECKED"  # discovered but never validated: never treated as valid (§2)


VALID_SESSION_STATUSES = {SessionStatus.ACTIVE, SessionStatus.VALID}


class SessionLocation(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    QUARANTINED = "quarantined"


class HealthState(StrEnum):
    HEALTHY = "Healthy"
    WARNING = "Warning"
    CRITICAL = "Critical"
    UNAVAILABLE = "Unavailable"


class ProxyProtocol(StrEnum):
    SOCKS5 = "socks5"
    SOCKS4 = "socks4"
    HTTP = "http"
    MTPROTO = "mtproto"


class ProxyStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    UP = "UP"
    DOWN = "DOWN"
    DISABLED = "DISABLED"


class TargetType(StrEnum):
    CHANNEL = "channel"
    GROUP = "group"
    MESSAGE = "message"
    ACCOUNT = "account"
    URL = "url"
    USERNAME = "username"
    MESSAGE_REFERENCE = "message_reference"


class TargetStatus(StrEnum):
    NEW = "NEW"
    UNDER_REVIEW = "UNDER_REVIEW"
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    ARCHIVED = "ARCHIVED"


class CaseStatus(StrEnum):
    DRAFT = "Draft"
    PENDING_REVIEW = "Pending Review"
    APPROVED = "Approved"
    READY = "Ready"
    SUBMITTED = "Submitted"
    AWAITING_RESPONSE = "Awaiting Response"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CLOSED = "Closed"


# Allowed state machine transitions for cases (§18/§25). Enforced in CaseService.
CASE_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.DRAFT: {CaseStatus.PENDING_REVIEW, CaseStatus.CLOSED},
    CaseStatus.PENDING_REVIEW: {CaseStatus.APPROVED, CaseStatus.DRAFT, CaseStatus.CLOSED},
    CaseStatus.APPROVED: {CaseStatus.READY, CaseStatus.DRAFT, CaseStatus.CLOSED},
    CaseStatus.READY: {CaseStatus.SUBMITTED, CaseStatus.APPROVED, CaseStatus.CLOSED},
    CaseStatus.SUBMITTED: {CaseStatus.AWAITING_RESPONSE, CaseStatus.FAILED, CaseStatus.COMPLETED, CaseStatus.CLOSED},
    CaseStatus.AWAITING_RESPONSE: {CaseStatus.COMPLETED, CaseStatus.FAILED, CaseStatus.CLOSED},
    CaseStatus.COMPLETED: {CaseStatus.CLOSED},
    CaseStatus.FAILED: {CaseStatus.READY, CaseStatus.CLOSED},
    CaseStatus.CLOSED: set(),
}


class CasePriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvidenceType(StrEnum):
    SCREENSHOT = "screenshot"
    URL = "url"
    MESSAGE = "message"
    FILE = "file"
    VIDEO = "video"
    DOCUMENT = "document"
    HASH = "hash"
    TIMESTAMP = "timestamp"


class SubmissionChannel(StrEnum):
    """Official channels only (§13). No account/session based submission exists."""

    MANUAL = "manual"                 # operator submits via official portal / in-app report and records it
    OFFICIAL_EMAIL = "official_email"  # abuse@/dmca@/stopCA@telegram.org, single recipient, explicit approval
    OFFICIAL_PORTAL = "official_portal"
    OFFICIAL_API = "official_api"     # placeholder: only if a documented official API exists (none assumed)


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    WAITING = "WAITING"
    CANCELLED = "CANCELLED"


class ErrorCategory(StrEnum):
    SESSION_ERROR = "SESSION_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROXY_ERROR = "PROXY_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER_ERROR = "SERVER_ERROR"
    SUBMISSION_ERROR = "SUBMISSION_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class AuditAction(StrEnum):
    CASE_CREATED = "Case Created"
    CASE_UPDATED = "Case Updated"
    CASE_STATUS_CHANGED = "Case Status Changed"
    TARGET_ADDED = "Target Added"
    TARGET_UPDATED = "Target Updated"
    EVIDENCE_ADDED = "Evidence Added"
    EVIDENCE_VERIFIED = "Evidence Verified"
    REASON_CHANGED = "Reason Changed"
    DRAFT_GENERATED = "Draft Generated"
    APPROVAL_GRANTED = "Approval Granted"
    SUBMISSION_CREATED = "Submission Created"
    SUBMISSION_SENT = "Submission Sent"
    RESPONSE_RECEIVED = "Response Received"
    CASE_CLOSED = "Case Closed"
    SESSION_DISCOVERED = "Session Discovered"
    SESSION_UPLOADED = "Session Uploaded"
    SESSION_CHECKED = "Session Checked"
    SESSION_MOVED = "Session Moved"
    SESSION_QUARANTINED = "Session Quarantined"
    PROXY_CHECKED = "Proxy Checked"
    EXPORT_CREATED = "Export Created"
    BACKUP_CREATED = "Backup Created"
    BACKUP_RESTORED = "Backup Restored"
    LOGIN = "Login"
    LOGIN_FAILED = "Login Failed"
    USER_CREATED = "User Created"
    SETTINGS_CHANGED = "Settings Changed"


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    AUDITOR = "auditor"
    VIEWER = "viewer"


__all__ = [n for n in dir() if n[0].isupper()]
