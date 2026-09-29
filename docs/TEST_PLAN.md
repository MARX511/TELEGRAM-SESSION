# TEST_PLAN — خطة الاختبار

## 1. التشغيل
```
.venv/bin/pytest -q            # PostgreSQL (tglegal_test) إن كان متاحًا على 5432، وإلا SQLite مؤقت
python -m benchmarks --db postgresql+asyncpg://tglegal:tglegal@localhost:5432/tglegal_test
```
كل الاختبارات تستخدم مجلدات مؤقتة وملفات Session **تركيبية** (مخطط SQLite صحيح بلا أي مفاتيح حقيقية) والمزوّد `simulation`. لا اتصال بـTelegram.

## 2. مصفوفة التغطية (§35)
| البند | الاختبار | النتيجة |
|---|---|---|
| Session Discovery | `test_session_discovery.py` (صيغ Telethon/Pyrogram، ملفات تالفة، ملفات مفقودة، idempotency) | ✅ |
| Session Validation | `test_session_validation.py` (VALID + ربط الحساب، BANNED → حجر فوري، فشل شبكي → حجر بعد العتبة، FloodWait → إيقاف مؤقت بلا تجاوز، تعطيل/تفعيل يعيد UNCHECKED، تشفير/فك، فحص جماعي بتزامن) | ✅ |
| Proxy Connectivity | `test_proxies_queue.py::test_proxy_connectivity_check` (UP بكمون، DOWN بخطأ، تعطيل) | ✅ |
| Database Integrity | `conftest` (create_all/truncate على PostgreSQL) + `test_backup_recovery.py` (integrity_check) | ✅ |
| Case Workflow | `test_case_workflow.py` (آلة الحالات، منع القفزات، اعتماد يتطلب مسودة+سبب، قفل المسودة، تاريخ كامل) | ✅ |
| Evidence Integrity | `test_evidence_exports.py` (SHA-256، كشف التلاعب، سلسلة حيازة، ZIP manifest) | ✅ |
| Submission Workflow | `test_case_workflow.py` (manual → WAITING → confirm → response → Completed؛ allow-list البريد؛ `.eml`؛ رفض `official_api`؛ لا قناة تستخدم Sessions) | ✅ |
| Retry Logic | `test_proxies_queue.py` (RETRYING مع backoff حتى max ثم FAILED، timeout قابل لإعادة المحاولة) | ✅ |
| Rate-Limit Handling | `test_session_validation.py::test_flood_wait…` + `test_queue…` (WAITING بموعد الخادم حرفيًا) | ✅ |
| Failure Recovery | `test_backup_recovery.py` (resume RUNNING→PENDING، reconcile ملفات، استعادة sessions) | ✅ |
| Export | `test_evidence_exports.py` (JSON/CSV/PDF/ZIP، لا مسارات مطلقة، Audit لكل تصدير) | ✅ |
| Authentication | `test_api_web_rbac.py` (401، JWT، كوكي اللوحة، تسجيل فشل الدخول) | ✅ |
| RBAC | `test_api_web_rbac.py` (viewer ممنوع من الكتابة، operator لا يعتمد، reviewer يعتمد، audit للمدقق) | ✅ |
| Audit | `test_api_web_rbac.py` (سلسلة أحداث القضية كاملة في السجل) | ✅ |
| Dashboard | `test_api_web_rbac.py::test_web_dashboard_pages` (كل الصفحات 200، إنشاء عبر النماذج، HTMX partial) | ✅ |

**النتيجة الحالية:** 30 passed (PostgreSQL 16).

## 3. اختبار الضغط (§36) — سعة التطبيق فقط
الأحجام: 10 / 25 / 50 / 100 / 200 / 500 ملف تركيبي. المقاييس: توليد الملفات، الاكتشاف (rows/s)، الفحص المتزامن (checks/s)، الطابور (enqueue + drain jobs/s)، استجابة API (`/sessions?limit=100`, `/sessions/stats`, `/sessions/health`, `/monitoring/analytics`) واللوحة (`/sessions`, `/`)، الذاكرة (RSS delta)، صفوف DB.

> النتائج في `benchmarks/results/latest.json` ومُلخّصة أدناه. هذه أرقام لقدرة البرنامج في بيئة التطوير (4 vCPU / 15 GB) ولا تعني شيئًا عن استقرار الحسابات على المنصة (§37).

| Sessions | Discover (s) | Check all (s) | Checks/s | Enqueue (s) | Queue drain (s) | Jobs/s | API list-100 (ms) | Web /sessions (ms) | RSS Δ (MB) | DB rows (checks/audit) |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 0.063 | 0.219 | 45.7 | 0.022 | 0.338 | 29.6 | 37.5 | 92.3 | 19.0 | 20/30 |
| 25 | 0.052 | 0.205 | 122.0 | 0.039 | 0.616 | 40.6 | 12.3 | 15.8 | 2.7 | 49/76 |
| 50 | 0.099 | 0.336 | 148.8 | 0.073 | 1.014 | 49.3 | 10.9 | 13.8 | 0.7 | 97/151 |
| 100 | 0.2 | 0.666 | 150.2 | 0.148 | 1.789 | 55.9 | 73.7 | 13.5 | 1.1 | 194/302 |
| 200 | 0.373 | 1.151 | 173.8 | 0.285 | 3.452 | 57.9 | 12.1 | 17.0 | 1.1 | 388/604 |
| 500 | 0.968 | 3.034 | 164.8 | 0.729 | 8.712 | 57.4 | 14.0 | 15.3 | 3.2 | 970/1510 |

التزامن: 8 · المزوّد: simulation · التاريخ: 2026-09-29T05:32:52Z

**القراءة:** التدرّج خطّي حتى 500 ملف؛ زمن الاكتشاف والفحص يتناسب مع العدد، واستجابة API/اللوحة ثابتة (~10–20ms) بفضل الترقيم، والذاكرة الإضافية هامشية. الحد العملي يأتي من `CAPACITY_*` وموارد الخادم وقاعدة البيانات، ويُعاد قياسه على البيئة المستهدفة.

## 4. اختبارات لا يمكن تنفيذها في بيئة التطوير الحالية
- المزوّد الحقيقي `telethon` (الشبكة إلى Telegram محجوبة): يُختبر في Staging بجلسات المُشغّل.
- الإرسال SMTP الفعلي: يحتاج خادم بريد معتمدًا.
- Docker Compose: لا daemon هنا؛ الملفات جاهزة.
