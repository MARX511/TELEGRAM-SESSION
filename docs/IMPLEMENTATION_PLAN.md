# IMPLEMENTATION_PLAN — خطة التنفيذ

## 1. المراحل المنفّذة (هذا الإصدار)
| # | المرحلة | الحالة |
|---|---|---|
| 0 | Discovery: فحص المستودع/البيئة/القنوات الرسمية، تحديد حد النطاق | ✅ |
| 1 | الأساس: إعدادات، enums، logging، DB + Alembic، Compose، `/healthz` | ✅ |
| 2 | Sessions: اكتشاف، تشفير، فحص (Simulation/Telethon)، صحة، حجر | ✅ |
| 3 | Proxy + مجموعات + طابور/عمّال (backoff, timeout, retry, cancel, resume) | ✅ |
| 4 | Targets / Cases / Reasons / Templates / Evidence + سلسلة الحيازة | ✅ |
| 5 | Submission الرسمي + Responses + Execution log + فئات الأخطاء | ✅ |
| 6 | Auth + RBAC + Audit شامل + حماية ملفات Session | ✅ |
| 7 | Web Dashboard + JSON API + CLI | ✅ |
| 8 | Monitoring + Backup/Restore + Recovery | ✅ |
| 9 | Tests (30) + Benchmark 10→500 + Exports PDF/ZIP + Docs | ✅ |

## 2. المتبقي لاعتماد Production الكامل
| البند | الجهد التقديري | ملاحظات |
|---|---|---|
| اختبار `telethon` على جلسات المُشغّل الحقيقية في Staging بشبكة مفتوحة | 2–3 أيام | فحص فقط؛ يتطلب API_ID/HASH |
| اعتماد SMTP الرسمي + اختبار الإرسال لعنوان واحد | 1 يوم | بعد موافقة قانونية |
| Redis كإشارة إيقاظ للعمّال (اختياري) + عمّال متعددي العمليات | 2 أيام | الطابور DB-backed يبقى المصدر |
| صفحة Users/Roles إدارية في اللوحة (إنشاء/تعطيل) | 1 يوم | متاحة الآن عبر API/CLI |
| تدوير مفتاح التشفير كأمر CLI | 0.5 يوم | |
| CI (GitHub Actions): pytest + benchmark صغير | 0.5 يوم | |
| تشغيل Docker Compose في بيئة بها Docker daemon والتحقق | 0.5 يوم | الملفات جاهزة |

## 3. النسخة المتقدمة (Advanced, §40)
Real-time dashboard (WebSocket/SSE)، Policy engine (قواعد لتوجيه السبب/القناة)، Evaluation framework، High-availability (عدة نسخ API + عمّال مستقلون + PgBouncer)، Observability (OpenTelemetry + Prometheus)، مساعد AI لصياغة المسودات (اختياري، بمراجعة بشرية).

## 4. التقديرات (§40) — هندسية تقريبية
| النسخة | المدة | ما تشمله |
|---|---|---|
| MVP | 1–2 أسبوع | Session registry/checks، DB محلية، Targets/Cases، Logs، JSON/CSV، CLI |
| Production Candidate | 3–6 أسابيع | + Web Dashboard، RBAC، Evidence، Audit، Queue/Workers، Backups، Recovery، Testing، Monitoring، Official submission adapters |
| Advanced | 6–10+ أسابيع | + ما ورد في البند 3 |

## 5. عوامل التكلفة (§41) — لا تُبنى على عدد Sessions وحده
Features · UI complexity · Database · Integrations (SMTP/Telethon) · Security (RBAC/تشفير/تدقيق) · Testing · Deployment · Support period · Maintenance.
تقسيم العرض: Development · Deployment · Documentation · Initial Support · Extended Maintenance.

## 6. الدعم (§42) — مقترح SLA
| الفئة | الاستجابة | الحل المستهدف |
|---|---|---|
| Critical bug (توقف الخدمة/فقدان بيانات) | 4 ساعات عمل | 1 يوم عمل |
| High priority | 1 يوم عمل | 3 أيام عمل |
| Normal bug | 2 يوم عمل | إصدار الصيانة التالي |
| Feature request | 5 أيام عمل | حسب الاتفاق |
ضمان إصلاح الأخطاء: 30 / 60 / 90 يومًا حسب العقد.

## 7. كيفية التشغيل
```
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env   # DATABASE_URL, APP_SECRET_KEY, SESSION_FILE_ENCRYPTION_KEY (app security keygen)
alembic upgrade head && app users create admin --role admin
app sessions scan && app sessions check --all && app serve
```
