# PROJECT_STATE — حالة المشروع

**التاريخ:** 2026-09-29 · **الفرع:** `claude/compassionate-goodall-gonbo3` · **المرحلة:** Production Candidate (الإصدار 0.1.0)

## 1. ملخص الاكتشاف (PHASE 0)
- المستودع كان فارغًا (ملف `.gitattributes` فقط). المشروع بُني من الصفر.
- البيئة: Python 3.11، PostgreSQL 16 (محلي)، Redis متاح، Docker daemon غير متاح في بيئة التطوير (Compose جاهز للنشر).
- **قيد شبكي:** `telegram.org` و`api.telegram.org` محجوبان عبر بروكسي البيئة (403). لذلك المزوّد الافتراضي `TELEGRAM_PROVIDER=simulation`، والمزوّد الحقيقي (`telethon`) اختياري ويقتصر على فحص الحالة/الهوية.
- القنوات الرسمية الموثّقة للبلاغ: `abuse@telegram.org`، `dmca@telegram.org`، `stopCA@telegram.org`، زر الإبلاغ داخل التطبيق، وصفحة الدعم. لا توجد API عامة موثّقة للبلاغات؛ لذلك القناة `official_api` موجودة كمكان محجوز يرفض التنفيذ.

## 2. حدّ النطاق (ثابت)
- لا يوجد أي مكوّن يرسل بلاغات عبر حسابات/Sessions، ولا تضخيم بلاغ عبر حسابات متعددة. المواصفة نفسها تمنع ذلك (§13، §27).
- Sessions تُدار كأصول للمُشغّل المُخوّل: اكتشاف، فحص صلاحية، صحة، حجر. لا عمليات أخرى عليها.
- لا تدوير Proxy/Session للتحايل؛ حدود الخادم (FloodWait) تُحترم حرفيًا (§7).

## 3. ما تم إنجازه
| المجال | الحالة | الملفات الرئيسية |
|---|---|---|
| إعدادات + أسرار + نموذج السعة | ✅ | `app/config.py` |
| Enums (حالات/أخطاء/أحداث) | ✅ | `app/domain/enums.py` |
| قاعدة البيانات (21 جدولًا + Alembic) | ✅ | `app/db/models/*`, `alembic/` |
| اكتشاف/فحص/صحة/حجر Sessions | ✅ | `app/services/sessions.py`, `app/telegram/*` |
| تشفير ملفات Session at-rest | ✅ | `app/security/session_crypto.py` |
| Proxies + مجموعات | ✅ | `app/services/proxies.py`, `groups.py` |
| طابور دائم + عمّال + backoff + إلغاء | ✅ | `app/workers/*` |
| Targets / Cases / Reasons / Templates | ✅ | `app/services/targets.py`, `cases.py`, `reasons.py`, `templates.py` |
| أدلة + سلسلة حيازة + تحقق سلامة + ZIP | ✅ | `app/services/evidence.py` |
| طبقة التسليم الرسمي (يدوي/بوابة/بريد رسمي) | ✅ | `app/submission/channels.py`, `app/services/submissions.py` |
| تصدير JSON/CSV/PDF/ZIP + تحليلات | ✅ | `app/services/exports.py`, `analytics.py` |
| Auth (argon2 + JWT) + RBAC + Audit | ✅ | `app/security/*`, `app/services/audit.py` |
| JSON API (v1) | ✅ | `app/api/*` |
| لوحة Web (Jinja2 + HTMX) | ✅ | `app/web/*` |
| CLI (Typer) | ✅ | `app/cli/main.py` |
| مراقبة + نسخ احتياطي/استعادة/تعافٍ | ✅ | `app/monitoring/metrics.py`, `app/backup/manager.py` |
| اختبارات (30 اختبارًا، PostgreSQL) | ✅ | `tests/` |
| Benchmark سعة التطبيق 10→500 | ✅ | `benchmarks/` |
| Docker Compose / Dockerfile | ✅ | `docker-compose.yml`, `Dockerfile` |

## 4. ما يُعرف أنه غير مكتمل / يحتاج بيئة أخرى
- المزوّد الحقيقي `telethon` لم يُختبر ضد Telegram (الشبكة محجوبة). يحتاج بيئة تسمح بالاتصال + `TELEGRAM_API_ID/HASH` + ملفات Session حقيقية للمُشغّل.
- إرسال SMTP إلى العنوان الرسمي لم يُختبر ضد خادم بريد حقيقي (مُعطّل افتراضيًا؛ يُنتج `.eml`).
- Redis غير مستخدم بعد كإشارة إيقاظ للعمّال (الطابور DB-backed وهذا كافٍ للنسخة الحالية).
- الواجهة الأمامية server-rendered (لا SPA)؛ لا Real-time (WebSocket) بعد — ضمن "Advanced Version".

## 5. نتائج التحقق
- `pytest`: 30 passed (PostgreSQL 16).
- CLI: سيناريو كامل (scan → check → case → evidence → draft → approve → submission → confirm → response → export → backup → audit) نُفّذ بنجاح.
- Benchmark (تزامن 8، simulation): 500 جلسة → اكتشاف 0.97s، فحص كامل 3.0s (~165/s)، تفريغ الطابور 8.7s (~57 job/s)، API list ~14ms، لوحة ~15ms، RSS +3MB. التفاصيل في `benchmarks/results/latest.json` و`TEST_PLAN.md`.

## 6. الخطوات التالية المقترحة
1. تشغيل في بيئة Staging بشبكة مفتوحة لاختبار `telethon` على جلسات المُشغّل الحقيقية (فحص فقط).
2. تفعيل SMTP الرسمي بعد اعتماد قانوني للعنوان المرسِل.
3. Real-time dashboard + Policy engine (Advanced Version).
