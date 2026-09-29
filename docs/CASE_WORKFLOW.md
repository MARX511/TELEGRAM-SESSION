# CASE_WORKFLOW — سير القضية والبلاغ الرسمي

## 1. آلة الحالات (§25) — `CASE_TRANSITIONS`
```
Draft ──► Pending Review ──► Approved ──► Ready ──► Submitted ──► Awaiting Response ──► Completed ──► Closed
  │             │                │          │           │                 │                          ▲
  └── Closed    └── Draft/Closed └── Draft  └── Approved└── Failed ───────┴── Failed ─► Ready/Closed ─┘
```
| الانتقال | الشرط | الصلاحية |
|---|---|---|
| → Pending Review | — | `cases:write` |
| → Approved | `draft_json` موجود + `reason_id` محدد | `cases:approve` (reviewer/admin) |
| → Ready | من Approved | `cases:write` |
| → Submitted | تلقائي عند تنفيذ Submission | `submissions:execute` |
| → Awaiting Response | تلقائي عند COMPLETED أو تأكيد يدوي | — |
| → Completed / Failed | من نتيجة الرد (`accepted…` / `rejected…`) | `submissions:write` |
| → Closed | من أي حالة (عدا Closed) | `cases:write` |

## 2. الأسباب (§10) — `reasons`
كتالوج بيانات (بذرة في `app/data/reasons_seed.json`)، مُصدَّر ومُدار عبر API/CLI. تعديل السبب يُنشئ إصدارًا جديدًا (`version+1`) ويُعطّل القديم مع الاحتفاظ به. كل سبب يحمل `policy_reference` و`official_channel_hint` (مثل `abuse@telegram.org`).

## 3. القوالب (§11) — `explanation_templates`
Jinja2 (Sandboxed) بالمتغيرات `case`, `target`, `reason`, `evidence`, `now`. الحقول: Subject, Summary, Reason, Evidence Summary, Requested Review, Reference, Additional Notes. `generate_draft` يملأ `case.draft_json`، و`update_draft` يسمح للمُشغّل بتعديل النص **قبل الاعتماد فقط** (يُقفل بعد Approved).

## 4. الأدلة (§12)
- ملفات: تُخزَّن في `EVIDENCE_ROOT/<case_id>/<evidence_id>__<name>` بصلاحية 0600، SHA-256 عند الإضافة.
- مراجع (URL/message/hash/timestamp): تُهشّ القيمة الكنسية.
- **Chain of custody:** `evidence_custody` يسجّل added / verified / accessed / exported مع الفاعل والوقت والهاش.
- **Integrity verification:** `verify_evidence` يعيد الحساب ويُحدّث `integrity_ok`؛ حزمة ZIP تحوي `manifest.json` بالهاشات وسلسلة الحيازة.

## 5. التسليم الرسمي (§13) — `app/submission/channels.py`
| القناة | ما يحدث | الحالة بعد التنفيذ |
|---|---|---|
| `manual` | يُولّد حزمة `.txt`؛ المُشغّل يقدّمها عبر الزر داخل التطبيق/البوابة ثم يؤكد بالمرجع | WAITING → COMPLETED عند `confirm` |
| `official_portal` | كالسابق مع تلميح البوابة (`in_app_report`, `telegram_support`) | WAITING |
| `official_email` | المستلم يجب أن يكون في allow-list الرسمية. إن كان SMTP مفعّلًا ومعتمَدًا → إرسال فعلي لمستلم واحد؛ وإلا يُنتج `.eml` للمُشغّل | COMPLETED أو WAITING |
| `official_api` | يرفض: لا API عامة موثّقة | FAILED |

قواعد ثابتة: بلاغ واحد مفتوح لكل قضية لكل قناة؛ التنفيذ يتطلب هوية مُعتمِد صريحة (`approved_by`) وتُدوَّن في Audit؛ **لا قناة تستخدم Sessions/حسابات**.

## 6. سجل التنفيذ والردود (§14)
`submissions` + `submission_attempts` (محاولة لكل تنفيذ بحالات PENDING/RUNNING/COMPLETED/FAILED/RETRYING/WAITING/CANCELLED) + `responses` (outcome, reference, body). الفشل يُسجَّل في `errors` بفئة `SUBMISSION_ERROR`؛ إعادة المحاولة محدودة بـ`CAPACITY_MAX_RETRIES`.

## 7. التاريخ (§18)
`case_events` يحفظ: Case Created، Target Added، Evidence Added، Reason Changed، Draft Generated، Draft Edited، Approval Granted، Submission Created، Submission Sent، Response Received، Case Closed — بالإضافة إلى `audit_logs` العام.

## 8. مثال CLI كامل
```
app targets add channel --url https://t.me/example --title Example
app cases create <target_id> "Illegal content" --reason illegal_content --explanation "..."
app cases evidence-add <case_id> url "post" --value https://t.me/example/5
app cases draft <case_id> && app cases approve <case_id> --as reviewer
app submissions create <case_id> --channel official_email --recipient abuse@telegram.org --execute
app submissions confirm <submission_id> --reference TG-123
app submissions respond <submission_id> accepted
app reports case-pdf <case_id> && app reports evidence-zip <case_id>
```
