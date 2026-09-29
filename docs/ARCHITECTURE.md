# ARCHITECTURE — المعمارية

## 1. الطبقات
```
┌──────────────────────────────────────────────────────────────────┐
│  Interfaces:  Web Dashboard (Jinja2+HTMX)  │  JSON API v1  │  CLI │
├──────────────────────────────────────────────────────────────────┤
│  Security:    Auth (argon2 + JWT)  ·  RBAC  ·  Audit  ·  Secrets   │
├──────────────────────────────────────────────────────────────────┤
│  Services:    sessions · groups · proxies · targets · cases         │
│               reasons · templates · evidence · submissions          │
│               exports · analytics · audit · errors                  │
├───────────────┬──────────────────────┬───────────────────────────┤
│  Telegram     │  Submission channels │  Workers / Queue           │
│  validator    │  manual / portal /   │  DB-backed jobs, backoff,  │
│  (simulation  │  official_email      │  timeout, retry, cancel,   │
│   | telethon) │  (allow-listed)      │  resume                    │
├───────────────┴──────────────────────┴───────────────────────────┤
│  Data:  PostgreSQL (SQLAlchemy 2 async + Alembic) · FS roots        │
│         sessions/{active,disabled,quarantined} · evidence_store     │
│         backups · exports                                           │
└──────────────────────────────────────────────────────────────────┘
```
كل الواجهات الثلاث تستدعي **نفس طبقة الخدمات**؛ لا منطق أعمال في الـrouters أو الـCLI.

## 2. خريطة الوحدات
| المسار | المسؤولية |
|---|---|
| `app/config.py` | إعدادات pydantic-settings؛ الأسرار من البيئة فقط؛ `CapacitySettings` (§39) |
| `app/domain/enums.py` | كل الحالات والتصنيفات + آلة حالات القضية `CASE_TRANSITIONS` |
| `app/db/` | `Base`, engine غير متزامن، Models (21 جدولًا) |
| `app/telegram/` | `validator.py` (واجهة `SessionValidator` + `CheckResult`)، `simulation.py`، `telethon_adapter.py`، `session_files.py` (كشف الصيغة دون اتصال) |
| `app/services/` | منطق الأعمال؛ كل دالة تأخذ `AsyncSession` وتُسجّل Audit |
| `app/submission/channels.py` | محوّلات القنوات الرسمية + `ReportPackage` |
| `app/workers/` | `JobQueue` (جدول `jobs`)، `WorkerPool`، `backoff`، `handlers` |
| `app/security/` | `auth.py`, `rbac.py`, `session_crypto.py` (Fernet)، `secrets.py` |
| `app/api/` | Routers تحت `/api/v1` |
| `app/web/` | Router + قوالب اللوحة |
| `app/cli/main.py` | Typer: sessions/proxies/groups/targets/cases/submissions/reports/audit/users/backups/security/db |
| `app/monitoring/metrics.py` | CPU/RAM/Disk/DB/Queue/Activity |
| `app/backup/manager.py` | Backup/Restore/Integrity/Recover |

## 3. تدفقات رئيسية
### فحص Session (§2)
```
DISCOVER (glob sessions/**/*.session[.enc]) → inspect (SQLite schema) → upsert registry
CHECK: enabled? rate_limited_until passed? → decrypt to 0600 temp (if .enc) → validator.check(path, proxy)
     → CheckResult → SessionCheck row → update status/health/counters → audit
     → RATE_LIMIT: rate_limited_until = now + server_wait (no bypass)
     → BANNED/EXPIRED/INVALID or consecutive_failures ≥ threshold: QUARANTINE (move file, enabled=False)
```
### القضية والبلاغ (§9–§14)
```
Target → Case(Draft) → Reason → Draft generated (template) → operator edits → Pending Review
→ Approved (reviewer, requires draft+reason) → Ready → Submission(channel, recipient)
→ execute (explicit approver) → WAITING (manual/portal/.eml) | COMPLETED (SMTP on) | FAILED→RETRYING
→ confirm_manual_sent(reference) → Awaiting Response → record_response → Completed/Failed → Closed
```
### الطابور (§6, §7)
```
enqueue(job_type, payload) → jobs(PENDING) → claim (FOR UPDATE SKIP LOCKED) → RUNNING
 → COMPLETED | RateLimited→WAITING(not_before=server delay) | retryable→RETRYING(backoff) | FAILED | CANCELLED
startup: resume_stale_running() (RUNNING→PENDING)
```

## 4. نموذج السعة (§39)
لا يوجد رقم ثابت. الحدود قابلة للتهيئة عبر `CAPACITY_*` وتُقاس بـ`python -m benchmarks`:
`session_check_concurrency`, `worker_count`, `queue_max`, `task_timeout_seconds`, `max_retries`, `backoff_*`.
**سعة التطبيق ≠ توافر الحسابات** (§37): الأولى هندسية وتُقاس؛ الثانية شأن المنصة وسياساتها.

## 5. أنماط النشر (§34)
| النمط | الوصف |
|---|---|
| Local | PostgreSQL محلي أو SQLite، `app serve`، العمّال داخل عملية الـAPI |
| Private server | Docker Compose (postgres + redis + app)؛ `WORKERS_ENABLED=false` في الـAPI و`app worker` كعملية مستقلة |
| Staging | نفس Compose مع `APP_ENV=staging`، `TELEGRAM_PROVIDER=telethon` (فحص فقط) |
| Production | `APP_ENV=production` (كوكي Secure)، أسرار من مدير أسرار، نسخ احتياطي مجدول |

## 6. قرارات تصميم
- الطابور DB-backed بدل Redis-only: يضمن البقاء بعد الأعطال والاستئناف (§31)؛ Redis اختياري كإشارة.
- Simulation كمزوّد افتراضي: اختبارات وقياسات حتمية بلا اتصال بالمنصة.
- القنوات الرسمية allow-list كبيانات في الكود (`OFFICIAL_EMAIL_RECIPIENTS`) وليست إعدادًا حرًا، لمنع الإرسال لعناوين غير رسمية.
