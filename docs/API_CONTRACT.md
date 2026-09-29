# API_CONTRACT — عقد واجهة البرمجة (v1)

- القاعدة: `/api/v1` · التوثيق التفاعلي: `/api/docs` · الصحة: `GET /healthz`
- المصادقة: `POST /auth/token` (OAuth2 form) أو `POST /auth/login` (JSON) → `Bearer <JWT>`. اللوحة تستخدم كوكي `tg_access` (HttpOnly).
- الأخطاء: `{"detail": "...", "category": "<ErrorCategory>"}` مع 400/401/403/404/409/422/429.
- الترقيم: `{"total", "limit", "offset", "items"}`.

| Method | Path | الصلاحية | ملاحظات |
|---|---|---|---|
| POST | `/auth/login` `/auth/token` | — | يسجّل Login/Login Failed في Audit |
| GET | `/auth/me` | مصادق | |
| GET/POST | `/auth/users` · POST `/auth/users/{id}/deactivate` | `users:manage` | |
| GET | `/sessions` | `sessions:read` | filters: status, health, location, group_id, search, sort, order |
| GET | `/sessions/stats` · `/sessions/health` · `/sessions/{id}` · `/sessions/{id}/health` · `/sessions/{id}/checks` | `sessions:read` | |
| POST | `/sessions/scan` | `sessions:write` | اكتشاف الملفات |
| POST | `/sessions/upload` | `sessions:write` | رفع ملفات `.session` أو `.zip` (multipart: `files`، و`check` لجدولة الفحص)؛ يُتحقق من كل ملف ويُتجاهل المكرّر |
| POST | `/sessions/check` | `sessions:check` | body: `session_ids|status|group_id|location, inline` — يضع مهام أو ينفّذ فورًا بحد التزامن |
| POST | `/sessions/{id}/check?force=` | `sessions:check` | |
| PATCH | `/sessions/{id}` | `sessions:write` | group_id, proxy_id, tags |
| POST | `/sessions/{id}/disable|enable|quarantine|encrypt` | `sessions:write` | |
| GET/POST | `/session-groups` · POST `/{id}/assign` · `/{id}/check` · `/{id}/enable?enabled=` | read: `sessions:read`، write: `sessions:write`/`sessions:check` | |
| GET | `/accounts` | `sessions:read` | |
| GET/POST | `/proxies` · POST `/{id}/check` · `/{id}/enable` | `proxies:read` / `proxies:write` | |
| GET/POST | `/targets` · GET `/targets/stats` · GET/PATCH `/targets/{id}` | `targets:read` / `targets:write` | |
| GET/POST | `/cases` · GET `/cases/stats` · GET/PATCH `/cases/{id}` | `cases:read` / `cases:write` | `{id}` يقبل UUID أو case_number |
| POST | `/cases/{id}/reason/{code}` · `/cases/{id}/draft` · PUT `/cases/{id}/draft` · GET `/cases/{id}/draft` | `cases:write` | |
| POST | `/cases/{id}/transition` `{to_status, note}` | `cases:write` (+`cases:approve` للاعتماد) | |
| POST | `/cases/{id}/approve` | `cases:approve` | |
| GET | `/cases/{id}/history` | `cases:read` | |
| GET/POST | `/reasons` · PUT `/reasons/{code}` · POST `/reasons/seed` | read: `cases:read`، write: `settings:manage` | |
| GET/POST | `/templates` | `cases:read` / `settings:manage` | |
| GET | `/cases/{id}/evidence` | `evidence:read` | |
| POST | `/cases/{id}/evidence/file` (multipart) · `/evidence/reference` (JSON) | `evidence:write` | |
| POST | `/cases/{id}/evidence/verify` · GET `/evidence/{eid}/download` | `evidence:read` | التنزيل يسجّل custody `accessed` |
| GET | `/submissions` · `/submissions/stats` · `/submissions/channels` · `/submissions/{id}` | `submissions:read` | |
| POST | `/submissions/cases/{case_id}` `{channel, recipient}` | `submissions:write` | القضية يجب أن تكون Ready |
| POST | `/submissions/{id}/execute` · `/confirm` | `submissions:execute` | execute يسجّل approved_by |
| POST | `/submissions/{id}/response` · `/cancel` | `submissions:write` | |
| GET/POST | `/exports` · POST `/exports/cases/{id}/pdf` · `/exports/cases/{id}/evidence-zip` · `/exports/cases/{id}/dossier` (حزمة رسمية للجهات الحكومية) · GET `/exports/{id}/download` | `reports:export` | |
| GET | `/audit` | `audit:read` | filters: action, actor, entity_id, case_id, session_id, since, until |
| GET | `/errors` | `monitoring:read` | |
| GET | `/monitoring/metrics` · `/monitoring/analytics` | `monitoring:read` / `cases:read` | |
| GET | `/jobs` · `/jobs/stats` · POST `/jobs/{id}/cancel` | `monitoring:read` / `sessions:write` | |
| GET/POST | `/backups` · POST `/backups/{type}` · `/{id}/verify` · `/{id}/restore` · GET `/backups/integrity` · POST `/backups/recover/run` | `backups:manage` | |

## أمثلة
```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"username":"admin","password":"..."}' | jq -r .access_token)
curl -s -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/sessions/scan
curl -s -H "Authorization: Bearer $TOKEN" -X POST localhost:8000/api/v1/sessions/check -H 'content-type: application/json' -d '{"inline": true}'
```
