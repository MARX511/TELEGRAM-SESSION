# SESSION_ARCHITECTURE — معمارية الجلسات

## 1. التخزين (§29)
```
sessions/
  active/        جلسات مُفعّلة قابلة للفحص
  disabled/      عُطّلت يدويًا
  quarantined/   فشلت (BANNED/EXPIRED/INVALID) أو تجاوزت عتبة الفشل — لا تُحذف
```
- الملفات المدعومة: `*.session` (Telethon أو Pyrogram — كلاهما SQLite) و`*.session.enc` (مشفّر).
- كشف الصيغة دون اتصال: `app/telegram/session_files.py` يفحص جداول SQLite (`entities` → Telethon، `peers` → Pyrogram). أي ملف غير SQLite أو بمخطط غير معروف = `INVALID` منذ الاكتشاف.
- التشفير at-rest: Fernet بمفتاح `SESSION_FILE_ENCRYPTION_KEY` (`app security keygen`). أثناء الفحص يُفكّ التشفير إلى ملف مؤقت بصلاحية 0600 ويُحذف مع ملف `-journal` في `finally`.
- الواجهة الأمامية والـAPI لا يقدّمان مسار الملف ولا محتواه إطلاقًا؛ التصدير يستثني `file_path`.

## 2. دورة الفحص (§2)
```
DISCOVER → LOAD → VALIDATE → CLASSIFY → STORE
```
1. `discover_sessions`: يفهرس الملفات، يحسب SHA-256 والحجم، يحدّد الموقع (active/disabled/quarantined)، ويُعلّم الصفوف التي اختفى ملفها `UNAVAILABLE`/`file missing`.
2. `check_session`: يرفض الجلسة المعطّلة؛ يرفض إعادة الفحص قبل انقضاء `rate_limited_until` (يرمي `RateLimited`)؛ يحل الـProxy بالترتيب Session → Account → Group؛ ينفّذ `validator.check` بمهلة `CAPACITY_TASK_TIMEOUT_SECONDS`.
3. النتيجة `CheckResult` تُحفظ في `session_checks` وتُحدّث الصف: status/health/counters/availability، وتُربط بـ`accounts` عند النجاح.
4. **لا تُعتبر الجلسة صالحة إلا بعد نجاح فحص** — الحالة الابتدائية `UNCHECKED`، وإعادة التفعيل تعيدها إلى `UNCHECKED`.

## 3. الحالات
| الحالة | المعنى | المصدر |
|---|---|---|
| `UNCHECKED` | مكتشفة ولم تُفحص | الاكتشاف |
| `VALID` / `ACTIVE` | فحص ناجح، الحساب مخوّل | المزوّد |
| `BANNED` | الحساب معطّل/محظور | AUTH_ERROR |
| `EXPIRED` | مفتاح الجلسة أُلغي | AUTH_ERROR |
| `INVALID` | ملف غير صالح أو غير مخوّل | VALIDATION/SESSION_ERROR |
| `CHECK_FAILED` | فشل تشغيلي (شبكة/بروكسي/خادم/FloodWait) | NETWORK/PROXY/SERVER/RATE_LIMIT |
| `UNAVAILABLE` | الملف مفقود | الاكتشاف |

## 4. الصحة (§3) — `compute_health`
- `Unavailable`: محجورة أو معطّلة أو `UNAVAILABLE`/`UNCHECKED`.
- `Critical`: حالة نهائية (BANNED/EXPIRED/INVALID) أو `consecutive_failures ≥ QUARANTINE_FAILURE_THRESHOLD`.
- `Warning`: FloodWait سارٍ، أو فشل متتالٍ > 0، أو `availability < 0.8` بعد ≥ 5 فحوص.
- `Healthy`: صالحة ولا فشل متتالٍ.
- `availability = (check_count − failure_count) / check_count`.

## 5. Rate-limit / FloodWait (§7)
```
DETECT (error_category = RATE_LIMIT, server_wait_seconds)
→ PAUSE: rate_limited_until = now + server_wait   (لا يُحتسب فشل صلاحية، لا حجر)
→ WAIT: أي فحص قبل الموعد يُرفض بـRateLimited، وفي الطابور تصبح المهمة WAITING بـnot_before = الموعد
→ RETRY WHEN PERMITTED
```
ممنوع: تدوير Proxy/Session، إعادة المحاولة المبكرة، طلبات متوازية لنفس الجلسة لتجاوز الحد.

## 6. الحجر (§38)
```
VALID → CHECK → FAILED → QUARANTINE (نقل الملف إلى quarantined/، enabled=False, health=Unavailable)
→ MARK UNAVAILABLE → REQUIRE OPERATOR ACTION (إعادة تفويض أو سحب)
```
- فوري للحالات النهائية؛ بعد العتبة للفشل التشغيلي المتكرر.
- البرنامج يستمر بالعمل بغض النظر عن عدد الجلسات المحجورة.

## 7. المجموعات والـProxy (§4, §5)
- المجموعة: فحص جماعي (`/session-groups/{id}/check` يضع مهمة لكل جلسة)، تعطيل/تفعيل جماعي، Proxy للمجموعة، `check_concurrency` خاص.
- الـProxy لغرض التوجيه/العزل الشبكي فقط؛ `check_proxy` يقيس الوصول TCP والكمون. لا يوجد أي "rotation".

## 8. السعة (§36, §37, §39)
`python -m benchmarks --sizes 10,25,50,100,200,500` يقيس: توليد الملفات، الاكتشاف، الفحص المتزامن، الطابور، استجابة API/اللوحة، الذاكرة. الناتج يعكس **قدرة البرنامج فقط**؛ توافر الحسابات الحقيقية غير مضمون ولا مُقاس.
