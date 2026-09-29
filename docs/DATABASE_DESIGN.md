# DATABASE_DESIGN — تصميم قاعدة البيانات

PostgreSQL (الإنتاج) عبر SQLAlchemy 2 async + asyncpg. SQLite (aiosqlite) مقبول للتطوير/الاختبار. الترحيلات عبر Alembic (`alembic/versions/…_initial_schema.py`). المفاتيح الأساسية `uuid4().hex` (32 حرفًا). كل الأزمنة `timestamptz` بتوقيت UTC.

## الجداول
| الجدول | الأعمدة الأساسية | علاقات/قيود |
|---|---|---|
| `users` | username (unique), password_hash (argon2), role, is_active, last_login_at | — |
| `session_groups` | name (unique), enabled, proxy_id→proxies, check_concurrency, settings_json | FK SET NULL |
| `accounts` | telegram_id (unique), username, phone_masked, display_name, proxy_id | — |
| `sessions` | file_path (unique), file_name, file_sha256, file_format, encrypted, location, status, health, enabled, counters (check/failure/consecutive), availability, rate_limited_until, last_* | FK account/group/proxy; index (status, location) |
| `session_checks` | session_id, provider, operator, started/finished, result_status, success, latency_ms, error_category, error_message, server_wait_seconds | FK CASCADE; index (session_id, started_at) |
| `proxies` | host, port, protocol, username, secret_ref, status, latency_ms, last_check | unique (host, port, protocol) |
| `targets` | target_type, url, username, telegram_id, title, description, status, tags | index (type, status) |
| `reasons` | code, version, name, policy_reference, default_explanation_template, official_channel_hint, active | unique (code, version) |
| `explanation_templates` | name (unique), reason_code, subject, summary, reason_text, evidence_summary, requested_review, reference, additional_notes | — |
| `cases` | case_number (unique), target_id→targets (RESTRICT), reason_id, template_id, title, explanation, legal_basis, reference_number, priority, status, assigned_operator, approved_by/at, draft_json, closed_at | index (status, priority) |
| `case_events` | case_id, event_type, actor, from_status, to_status, details_json, created_at | FK CASCADE; index (case_id, created_at) |
| `evidence` | case_id, target_id, evidence_type, title, storage_path, sha256, external_url, captured_at, added_by, integrity_ok, last_verified_at | index (case_id, type) |
| `evidence_custody` | evidence_id, action (added/verified/accessed/exported), actor, at, sha256_at_time, notes | FK CASCADE |
| `submissions` | case_id, target_id, channel, recipient, operator, package_json, status, started/finished, result, error_code/message, reference_number, approved_by | index (case_id, status) |
| `submission_attempts` | submission_id, attempt_no, channel, operator, started/finished, status, result, error_* | FK CASCADE |
| `responses` | submission_id, received_at, source, outcome, reference_number, body, recorded_by | FK CASCADE |
| `errors` | category (§16), message, component, session_id, case_id, submission_id, operator, provider, details_json, occurred_at | index (category, occurred_at) |
| `audit_logs` | action, actor, entity_type/id, session_id, case_id, provider, result, reason, details_json, ip_address, at | index (action, at), (actor, at) |
| `jobs` | job_type, payload_json, status, attempts, max_attempts, not_before, started/finished, worker, result_json, error_* | index (status, created_at) |
| `exports` | export_type, fmt, file_path, sha256, size_bytes, requested_by, filters_json | — |
| `backups` | backup_type, file_path, sha256, size_bytes, created_by, restored_at | — |

## القيم المعدودة (Enums) المخزّنة كنص
- `sessions.status`: ACTIVE, VALID, BANNED, INVALID, EXPIRED, UNAVAILABLE, CHECK_FAILED, UNCHECKED
- `sessions.health`: Healthy, Warning, Critical, Unavailable · `sessions.location`: active, disabled, quarantined
- `cases.status`: Draft, Pending Review, Approved, Ready, Submitted, Awaiting Response, Completed, Failed, Closed
- `submissions.status` / `jobs.status`: PENDING, RUNNING, COMPLETED, FAILED, RETRYING, WAITING, CANCELLED
- `errors.category`: SESSION_ERROR, AUTH_ERROR, NETWORK_ERROR, PROXY_ERROR, VALIDATION_ERROR, RATE_LIMIT, SERVER_ERROR, SUBMISSION_ERROR, DATABASE_ERROR, UNKNOWN_ERROR

## الترحيل والصيانة
- `alembic upgrade head` عند كل نشر (Compose ينفّذها تلقائيًا). ترحيلات جديدة: `alembic revision --autogenerate -m "..."`.
- النسخ الاحتياطي: `pg_dump -Fc` عبر `app backups create database`؛ الاستعادة `pg_restore --clean --if-exists`.
- فحص السلامة: `app backups integrity` (اتصال DB + مطابقة سجل Sessions مع القرص + هاشات ملفات النسخ).
- لا تُخزَّن كلمات مرور Proxy: `secret_ref` يشير إلى متغير بيئة `SECRET_<REF>`.
