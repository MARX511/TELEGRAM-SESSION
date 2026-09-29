"""Pydantic DTOs for the JSON API (see docs/API_CONTRACT.md)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[Any]


# --- auth / users
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginIn(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8)
    role: str = "viewer"
    full_name: str | None = None


class UserOut(ORM):
    id: str
    username: str
    full_name: str | None
    role: str
    is_active: bool
    last_login_at: datetime | None


# --- sessions
class SessionOut(ORM):
    id: str
    file_name: str
    location: str
    file_format: str | None
    encrypted: bool
    account_id: str | None
    group_id: str | None
    proxy_id: str | None
    username: str | None
    telegram_id: int | None
    phone_masked: str | None
    status: str
    health: str
    enabled: bool
    last_check: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    last_error_category: str | None
    failure_count: int
    consecutive_failures: int
    check_count: int
    availability: float
    rate_limited_until: datetime | None
    tags: str | None
    created_at: datetime
    updated_at: datetime


class SessionCheckOut(ORM):
    id: str
    session_id: str
    provider: str
    operator: str | None
    started_at: datetime
    finished_at: datetime | None
    result_status: str
    success: bool
    latency_ms: int | None
    error_category: str | None
    error_message: str | None
    server_wait_seconds: int | None


class BulkCheckIn(BaseModel):
    session_ids: list[str] | None = None
    status: str | None = None
    group_id: str | None = None
    location: str | None = "active"
    inline: bool = False  # run now (bounded by concurrency) instead of enqueueing jobs


class SessionUpdate(BaseModel):
    group_id: str | None = None
    proxy_id: str | None = None
    tags: str | None = None


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    proxy_id: str | None = None
    check_concurrency: int | None = None


class GroupOut(ORM):
    id: str
    name: str
    description: str | None
    enabled: bool
    proxy_id: str | None
    check_concurrency: int | None
    created_at: datetime


class AccountOut(ORM):
    id: str
    telegram_id: int | None
    username: str | None
    phone_masked: str | None
    display_name: str | None
    proxy_id: str | None
    created_at: datetime


# --- proxies
class ProxyCreate(BaseModel):
    host: str
    port: int
    protocol: str = "socks5"
    name: str | None = None
    username: str | None = None
    secret_ref: str | None = None


class ProxyOut(ORM):
    id: str
    name: str | None
    host: str
    port: int
    protocol: str
    username: str | None
    status: str
    enabled: bool
    last_check: datetime | None
    latency_ms: int | None
    last_error: str | None


# --- targets
class TargetCreate(BaseModel):
    target_type: str
    url: str | None = None
    username: str | None = None
    telegram_id: int | None = None
    title: str | None = None
    description: str | None = None
    tags: str | None = None


class TargetUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    tags: str | None = None


class TargetOut(ORM):
    id: str
    target_type: str
    url: str | None
    username: str | None
    telegram_id: int | None
    title: str | None
    description: str | None
    status: str
    tags: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


# --- reasons / templates
class ReasonIn(BaseModel):
    code: str
    name: str
    description: str | None = None
    policy_reference: str | None = None
    default_explanation_template: str | None = None
    official_channel_hint: str | None = None


class ReasonOut(ORM):
    id: str
    code: str
    name: str
    description: str | None
    policy_reference: str | None
    default_explanation_template: str | None
    official_channel_hint: str | None
    active: bool
    version: int


class TemplateIn(BaseModel):
    name: str
    reason_code: str | None = None
    subject: str
    summary: str
    reason_text: str
    evidence_summary: str
    requested_review: str
    reference: str
    additional_notes: str | None = None


class TemplateOut(ORM):
    id: str
    name: str
    reason_code: str | None
    subject: str
    summary: str
    reason_text: str
    evidence_summary: str
    requested_review: str
    reference: str
    additional_notes: str | None
    active: bool


# --- cases
class CaseCreate(BaseModel):
    target_id: str
    title: str
    reason_code: str | None = None
    explanation: str | None = None
    legal_basis: str | None = None
    priority: str = "NORMAL"
    assigned_operator: str | None = None


class CaseUpdate(BaseModel):
    title: str | None = None
    explanation: str | None = None
    legal_basis: str | None = None
    priority: str | None = None
    assigned_operator: str | None = None
    reference_number: str | None = None


class DraftEdit(BaseModel):
    subject: str | None = None
    summary: str | None = None
    reason: str | None = None
    evidence_summary: str | None = None
    requested_review: str | None = None
    reference: str | None = None
    additional_notes: str | None = None


class TransitionIn(BaseModel):
    to_status: str
    note: str | None = None


class CaseEventOut(ORM):
    id: str
    event_type: str
    actor: str | None
    from_status: str | None
    to_status: str | None
    details_json: str | None
    created_at: datetime


class CaseOut(ORM):
    id: str
    case_number: str
    target_id: str
    reason_id: str | None
    template_id: str | None
    title: str
    explanation: str | None
    legal_basis: str | None
    reference_number: str | None
    priority: str
    status: str
    assigned_operator: str | None
    created_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    draft_json: str | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# --- evidence
class ReferenceEvidenceIn(BaseModel):
    evidence_type: str
    title: str
    value: str
    description: str | None = None
    captured_at: datetime | None = None


class CustodyOut(ORM):
    action: str
    actor: str | None
    at: datetime
    sha256_at_time: str | None
    notes: str | None


class EvidenceOut(ORM):
    id: str
    case_id: str
    evidence_type: str
    title: str
    description: str | None
    original_name: str | None
    mime_type: str | None
    size_bytes: int | None
    sha256: str | None
    external_url: str | None
    captured_at: datetime | None
    added_by: str | None
    integrity_ok: bool | None
    last_verified_at: datetime | None
    created_at: datetime
    custody: list[CustodyOut] = []


# --- submissions
class SubmissionCreate(BaseModel):
    channel: str = "manual"
    recipient: str | None = None


class ConfirmIn(BaseModel):
    reference_number: str | None = None
    note: str | None = None


class ResponseIn(BaseModel):
    outcome: str
    body: str | None = None
    reference_number: str | None = None
    source: str | None = None


class AttemptOut(ORM):
    attempt_no: int
    channel: str
    operator: str | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    result: str | None
    error_code: str | None
    error_message: str | None


class ResponseOut(ORM):
    id: str
    received_at: datetime
    source: str | None
    outcome: str | None
    reference_number: str | None
    body: str | None
    recorded_by: str | None


class SubmissionOut(ORM):
    id: str
    case_id: str
    target_id: str
    channel: str
    recipient: str | None
    operator: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    result: str | None
    error_code: str | None
    error_message: str | None
    reference_number: str | None
    approved_by: str | None
    created_at: datetime
    attempts: list[AttemptOut] = []
    responses: list[ResponseOut] = []


# --- misc
class ExportIn(BaseModel):
    export_type: str
    fmt: str = "json"
    filters: dict | None = None


class ExportOut(ORM):
    id: str
    export_type: str
    fmt: str
    file_path: str
    sha256: str | None
    size_bytes: int | None
    requested_by: str | None
    created_at: datetime


class AuditOut(ORM):
    id: str
    action: str
    actor: str | None
    entity_type: str | None
    entity_id: str | None
    session_id: str | None
    case_id: str | None
    provider: str | None
    result: str | None
    reason: str | None
    details_json: str | None
    at: datetime


class ErrorOut(ORM):
    id: str
    category: str
    message: str
    component: str | None
    session_id: str | None
    case_id: str | None
    submission_id: str | None
    operator: str | None
    provider: str | None
    occurred_at: datetime


class JobOut(ORM):
    id: str
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    not_before: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    worker: str | None
    error_category: str | None
    error_message: str | None
    requested_by: str | None
    created_at: datetime


class BackupOut(ORM):
    id: str
    backup_type: str
    file_path: str
    sha256: str | None
    size_bytes: int | None
    created_by: str | None
    created_at: datetime
    restored_at: datetime | None
