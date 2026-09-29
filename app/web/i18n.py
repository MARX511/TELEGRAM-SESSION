"""Dashboard localisation: Arabic (default, RTL) and English (LTR).

English UI strings are the message keys: `_("Sessions")` returns the Arabic string when the request language is
Arabic and the key itself otherwise, so a missing translation degrades to English instead of breaking. Enum values
coming from the database (statuses, types, audit actions ...) are displayed through `label()`, never raw.
tests/test_web_i18n_ui.py asserts every `_()` key used in the templates and the router has an Arabic entry here."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request

LANGS = ("ar", "en")
DEFAULT_LANG = "ar"
THEMES = ("dark", "light")
DEFAULT_THEME = "dark"
LANG_COOKIE = "lang"
THEME_COOKIE = "theme"


def get_lang(request: Request) -> str:
    value = request.cookies.get(LANG_COOKIE, "")
    return value if value in LANGS else DEFAULT_LANG


def get_theme(request: Request) -> str:
    value = request.cookies.get(THEME_COOKIE, "")
    return value if value in THEMES else DEFAULT_THEME


def direction(lang: str) -> str:
    return "rtl" if lang == "ar" else "ltr"


def translator(lang: str) -> Callable[[str], str]:
    if lang == "ar":
        return lambda key: AR.get(key, key)
    return lambda key: key


# --------------------------------------------------------------------------------------------- enum display labels
AR_LABELS: dict[str, str] = {
    # session status
    "ACTIVE": "نشط", "VALID": "صالح", "BANNED": "محظور", "INVALID": "غير صالح", "EXPIRED": "منتهي",
    "UNAVAILABLE": "غير متاح", "CHECK_FAILED": "فشل الفحص", "UNCHECKED": "لم يُفحص",
    # health
    "Healthy": "سليم", "Warning": "تحذير", "Critical": "حرج", "Unavailable": "غير متاح",
    # session storage location
    "active": "نشط", "disabled": "معطّل", "quarantined": "محجور",
    # proxies
    "UNKNOWN": "غير معروف", "UP": "متصل", "DOWN": "منقطع", "DISABLED": "معطّل",
    "socks5": "SOCKS5", "socks4": "SOCKS4", "http": "HTTP", "mtproto": "MTProto",
    # targets
    "channel": "قناة", "group": "مجموعة", "message": "رسالة", "account": "حساب", "url": "رابط",
    "username": "اسم مستخدم", "message_reference": "مرجع رسالة",
    "NEW": "جديد", "UNDER_REVIEW": "قيد المراجعة", "RESOLVED": "تمت المعالجة", "ARCHIVED": "مؤرشف",
    # cases
    "Draft": "مسودة", "Pending Review": "بانتظار المراجعة", "Approved": "معتمد", "Ready": "جاهز",
    "Submitted": "مُقدَّم", "Awaiting Response": "بانتظار الرد", "Completed": "مكتمل", "Failed": "متعثّر",
    "Closed": "مغلق",
    "LOW": "منخفض", "NORMAL": "عادي", "HIGH": "مرتفع", "CRITICAL": "حرج",
    # evidence
    "screenshot": "لقطة شاشة", "file": "ملف", "video": "فيديو", "document": "مستند", "hash": "بصمة رقمية",
    "timestamp": "طابع زمني",
    "added": "إضافة", "verified": "تحقّق", "accessed": "اطّلاع", "exported": "تصدير", "moved": "نقل",
    # submission channels / execution
    "manual": "يدوي", "official_email": "بريد رسمي", "official_portal": "بوابة رسمية", "official_api": "واجهة رسمية",
    "PENDING": "معلّق", "RUNNING": "قيد التنفيذ", "COMPLETED": "مكتمل", "FAILED": "فشل", "RETRYING": "إعادة المحاولة",
    "WAITING": "بانتظار التأكيد", "CANCELLED": "ملغى",
    "accepted": "مقبول", "rejected": "مرفوض", "info_requested": "طُلبت معلومات", "pending": "قيد الانتظار",
    "info": "معلومة", "OK": "ناجح",
    # error categories
    "SESSION_ERROR": "خطأ جلسة", "AUTH_ERROR": "خطأ مصادقة", "NETWORK_ERROR": "خطأ شبكة", "PROXY_ERROR": "خطأ بروكسي",
    "VALIDATION_ERROR": "خطأ تحقّق", "RATE_LIMIT": "تجاوز حد الطلبات", "SERVER_ERROR": "خطأ خادم",
    "SUBMISSION_ERROR": "خطأ تسليم", "DATABASE_ERROR": "خطأ قاعدة بيانات", "UNKNOWN_ERROR": "خطأ غير معروف",
    # audit actions / case events
    "Case Created": "إنشاء قضية", "Case Updated": "تحديث قضية", "Case Status Changed": "تغيير حالة قضية",
    "Target Added": "إضافة هدف", "Target Updated": "تحديث هدف", "Evidence Added": "إضافة دليل",
    "Evidence Verified": "التحقق من دليل", "Reason Changed": "تغيير السبب", "Draft Generated": "توليد المسودة",
    "Draft Edited": "تعديل المسودة", "Approval Granted": "منح الاعتماد", "Submission Created": "إنشاء تسليم",
    "Submission Sent": "إرسال تسليم", "Response Received": "استلام رد", "Case Closed": "إغلاق قضية",
    "Session Discovered": "اكتشاف جلسة", "Session Uploaded": "رفع جلسات", "Session Checked": "فحص جلسة", "Session Moved": "نقل جلسة",
    "Session Quarantined": "حجر جلسة", "Proxy Checked": "فحص بروكسي", "Export Created": "إنشاء تصدير",
    "Backup Created": "إنشاء نسخة احتياطية", "Backup Restored": "استعادة نسخة احتياطية", "Login": "تسجيل دخول",
    "Login Failed": "فشل تسجيل الدخول", "User Created": "إنشاء مستخدم", "Settings Changed": "تغيير الإعدادات",
    # roles
    "admin": "مدير النظام", "operator": "مشغّل", "reviewer": "مراجِع", "auditor": "مدقّق", "viewer": "مشاهد",
    # backups / exports
    "database": "قاعدة البيانات", "sessions": "الجلسات", "config": "الإعدادات", "evidence": "الأدلة",
    "targets": "الأهداف", "cases": "القضايا", "submissions": "التسليمات", "audit": "سجل التدقيق",
    "errors": "الأخطاء", "case_pdf": "تقرير قضية PDF", "evidence_zip": "حزمة أدلة ZIP",
    "unspecified": "غير محدد",
}

EN_LABELS: dict[str, str] = {
    "CHECK_FAILED": "Check failed", "UNCHECKED": "Unchecked", "UNDER_REVIEW": "Under review",
    "official_email": "Official email", "official_portal": "Official portal", "official_api": "Official API",
    "message_reference": "Message reference", "info_requested": "Info requested", "RATE_LIMIT": "Rate limit",
    "case_pdf": "Case PDF", "evidence_zip": "Evidence ZIP", "socks5": "SOCKS5", "socks4": "SOCKS4", "http": "HTTP",
    "mtproto": "MTProto", "UP": "Up", "DOWN": "Down", "OK": "OK", "WAITING": "Awaiting confirmation",
    "hash": "Hash", "url": "URL", "audit": "Audit log",
}


def _pretty(value: str) -> str:
    text = value.replace("_", " ")
    return text.capitalize() if (text.isupper() or text.islower()) else text


def label(lang: str, value: Any) -> str:
    if value is None or value == "":
        return "—"
    key = str(getattr(value, "value", value))
    if lang == "ar":
        return AR_LABELS.get(key) or EN_LABELS.get(key) or _pretty(key)
    return EN_LABELS.get(key) or _pretty(key)


# seeded reason catalogue (app/data/reasons_seed.json): Arabic display names by code; custom reasons keep their name
AR_REASONS: dict[str, str] = {
    "illegal_content": "محتوى غير قانوني",
    "child_safety": "حماية الأطفال / مواد استغلال الأطفال",
    "terrorism_violence": "محتوى إرهابي / تحريض على العنف",
    "copyright": "انتهاك حقوق النشر (DMCA)",
    "fraud_scam": "احتيال / نصب",
    "impersonation": "انتحال شخصية",
    "personal_data": "نشر بيانات شخصية",
    "spam": "رسائل مزعجة / إرسال جماعي غير مرغوب",
}


def reason_label(lang: str, reason: Any) -> str:
    if reason is None:
        return "—"
    code, name = getattr(reason, "code", None), getattr(reason, "name", str(reason))
    return AR_REASONS.get(code, name) if lang == "ar" else name


def reason_code_label(lang: str, code: str) -> str:
    if lang == "ar":
        return AR_REASONS.get(code) or AR_LABELS.get(code) or code
    return EN_LABELS.get(code) or _pretty(code)


# --------------------------------------------------------------------------------------------- status -> colour tone
_TONE_GROUPS = {
    "ok": ["VALID", "ACTIVE", "Healthy", "COMPLETED", "Completed", "UP", "accepted", "RESOLVED", "active", "OK"],
    "bad": ["BANNED", "INVALID", "EXPIRED", "Critical", "FAILED", "Failed", "DOWN", "rejected", "CRITICAL", "quarantined"],
    "warn": ["CHECK_FAILED", "Warning", "WAITING", "RETRYING", "Pending Review", "UNDER_REVIEW", "HIGH", "info_requested"],
    "info": ["PENDING", "RUNNING", "Approved", "Ready", "NEW"],
    "accent3": ["Submitted", "Awaiting Response"],
    "accent": ["admin"],
}
STATUS_TONES: dict[str, str] = {v: tone for tone, values in _TONE_GROUPS.items() for v in values}


# --------------------------------------------------------------------------------------------- UI strings (en -> ar)
AR: dict[str, str] = {
    # shell / navigation
    "Dashboard": "لوحة التحكم", "Sessions": "الجلسات", "Accounts": "الحسابات", "Proxies": "البروكسيات",
    "Targets": "الأهداف", "Cases": "القضايا", "Submissions": "التسليمات", "Errors": "الأخطاء", "Audit": "التدقيق",
    "Reports": "التقارير", "Settings": "الإعدادات", "Operations": "العمليات", "Casework": "العمل على القضايا",
    "Oversight": "الرقابة", "Main navigation": "التنقل الرئيسي", "Menu": "القائمة", "Sign out": "تسجيل الخروج",
    "System online": "النظام يعمل", "Dismiss": "إغلاق", "Switch language": "تبديل اللغة", "Switch theme": "تبديل المظهر",
    "Sessions · Cases · Official reporting": "الجلسات · القضايا · البلاغات الرسمية",
    "Pagination": "التنقل بين الصفحات", "Previous": "السابق", "Next": "التالي", "Page {page} of {pages}": "صفحة {page} من {pages}",
    "View all": "عرض الكل",
    # login
    "Sign in": "تسجيل الدخول", "Username": "اسم المستخدم", "Password": "كلمة المرور", "Show password": "إظهار كلمة المرور",
    "Welcome back. Enter your credentials to continue.": "مرحبًا بعودتك. أدخل بيانات الدخول للمتابعة.",
    "Invalid username or password": "اسم المستخدم أو كلمة المرور غير صحيحة",
    "Professional management of": "إدارة احترافية",
    "sessions, cases and official reports": "للجلسات والقضايا والبلاغات الرسمية",
    "A secure local platform: encrypted session files, evidence with a chain of custody, and a full audit trail for every action.":
        "منصّة محلية آمنة: ملفات جلسات مشفّرة، وأدلة بسلسلة حيازة موثّقة، وسجل تدقيق كامل لكل إجراء.",
    "Session files encrypted at rest": "تشفير ملفات الجلسات أثناء التخزين",
    "Evidence integrity and chain of custody": "سلامة الأدلة وسلسلة الحيازة",
    "Official reporting channels only, fully audited": "قنوات بلاغ رسمية فقط، مع تدقيق كامل",
    "Runs locally on your computer · no external services": "تعمل محليًا على جهازك · بلا خدمات خارجية",
    "Encrypted": "مشفّرة", "Fully audited": "مدقّقة بالكامل", "Local only": "محلية فقط",
    "All rights reserved": "جميع الحقوق محفوظة",
    # dashboard
    "Control center": "مركز التحكم", "Welcome back,": "مرحبًا بعودتك،",
    "Here is the live state of your sessions, cases and official submissions.": "هذه الحالة الحيّة لجلساتك وقضاياك وتسليماتك الرسمية.",
    "Scan sessions": "فحص مجلد الجلسات", "New case": "قضية جديدة",
    "Registered sessions": "الجلسات المسجّلة", "Healthy sessions": "الجلسات السليمة",
    "Average availability {pct}%": "متوسط التوافر {pct}%", "Open cases": "القضايا المفتوحة", "{n} closed": "{n} مغلقة",
    "Completed submissions": "التسليمات المكتملة", "{n} awaiting confirmation": "{n} بانتظار التأكيد",
    "Session health": "صحة الجلسات", "sessions": "جلسة", "Case pipeline": "مسار القضايا",
    "No sessions yet. Put .session files under sessions/active and scan.": "لا توجد جلسات بعد. ضع ملفات ‎.session‎ في المجلد sessions/active ثم افحص.",
    "Application capacity is not account availability: platform limits still apply.":
        "سعة التطبيق لا تعني توافر الحسابات: حدود المنصة تبقى سارية.",
    "Total": "الإجمالي", "Channels": "القنوات", "Groups": "المجموعات", "Messages": "الرسائل", "Closed cases": "القضايا المغلقة",
    "Failure causes (7 days)": "أسباب الفشل (7 أيام)", "No failures recorded in the last 7 days.": "لم تُسجَّل أي أخطاء خلال آخر 7 أيام.",
    "No cases yet.": "لا توجد قضايا بعد.",
    # sessions
    "Registry, validation and health of your authorised session files.": "سجل ملفات الجلسات المخوّلة وفحصها وصحتها.",
    "Scan folder": "فحص المجلد", "Bulk health check": "فحص صحي جماعي", "Search": "بحث", "File, username or tag": "الملف أو اسم المستخدم أو الوسم",
    "Status": "الحالة", "Any status": "كل الحالات", "Health": "الصحة", "Any health": "كل حالات الصحة", "Location": "الموقع",
    "Any location": "كل المواقع", "Filter": "تصفية", "File": "الملف", "User": "المستخدم", "Last check": "آخر فحص",
    "Failures": "الإخفاقات", "Availability": "التوافر", "Last error": "آخر خطأ", "Actions": "الإجراءات",
    "Encrypted at rest": "مشفّر أثناء التخزين", "Check": "فحص", "Disable": "تعطيل", "Enable": "تفعيل", "Quarantine": "حجر",
    "Scan complete: {found} found, {new} new, {missing} missing": "اكتمل الفحص: {found} ملفًا، منها {new} جديدة، و{missing} مفقودة",
    "{n} health checks queued": "أُضيفت {n} فحوص صحية إلى قائمة الانتظار",
    "{file}: {status}": "{file}: {status}", "{file} disabled": "عُطّلت الجلسة {file}",
    "{file} enabled (a new check is required)": "فُعّلت الجلسة {file} (يلزم فحص جديد)",
    "{file} moved to quarantine": "نُقلت الجلسة {file} إلى الحجر",
    # accounts / proxies
    "Telegram identities resolved from successfully validated sessions.": "هويات تيليجرام المستخرجة من الجلسات التي نجح فحصها.",
    "Telegram ID": "معرّف تيليجرام", "Phone": "الهاتف", "Name": "الاسم", "Proxy": "البروكسي", "First seen": "أول ظهور",
    "Accounts appear after a successful session check.": "تظهر الحسابات بعد نجاح فحص الجلسة.",
    "Used for routing and network isolation only. No rotation, no evasion.": "للتوجيه وعزل الشبكة فقط. لا تدوير ولا تحايل.",
    "Optional": "اختياري", "Host": "المضيف", "Port": "المنفذ", "Protocol": "البروتوكول", "Add proxy": "إضافة بروكسي",
    "Endpoint": "نقطة الاتصال", "Latency": "زمن الاستجابة", "No proxies configured.": "لا توجد بروكسيات مُعدّة.",
    "Proxy added": "أُضيف البروكسي", "{endpoint} → {status}": "{endpoint}: {status}",
    # targets
    "Channels, groups, accounts and messages under review.": "القنوات والمجموعات والحسابات والرسائل قيد المراجعة.",
    "Add target": "إضافة هدف", "Type": "النوع", "Link": "الرابط", "Title": "العنوان", "Tags": "الوسوم",
    "Comma separated": "مفصولة بفواصل", "Link, username, ID or title": "الرابط أو اسم المستخدم أو المعرّف أو العنوان",
    "Any type": "كل الأنواع", "Target": "الهدف", "Added": "أُضيف", "No targets yet.": "لا توجد أهداف بعد.",
    "Target {id} added": "أُضيف الهدف {id}",
    # cases
    "Every case moves Draft → Review → Approval → Official submission → Response → Closed.":
        "تمرّ كل قضية بالمراحل: مسودة ← مراجعة ← اعتماد ← تسليم رسمي ← رد ← إغلاق.",
    "Choose a target": "اختر هدفًا", "Reason": "السبب", "Priority": "الأولوية", "Explanation": "الشرح",
    "Legal basis": "الأساس القانوني", "Create case": "إنشاء القضية", "Case number, title or reference": "رقم القضية أو العنوان أو المرجع",
    "Number": "الرقم", "Operator": "المشغّل", "Updated": "آخر تحديث", "Case {number} created": "أُنشئت القضية {number}",
    "Reference": "المرجع", "Export PDF": "تصدير PDF", "Evidence ZIP": "حزمة الأدلة ZIP", "Workflow": "سير العمل",
    "Set reason": "تعيين السبب", "Generate draft": "توليد المسودة",
    "Submissions go through official channels only, filed once by an authorised operator.":
        "التسليم عبر القنوات الرسمية فقط، ولمرة واحدة من مشغّل مخوّل.",
    "Report draft": "مسودة البلاغ", "(editable before approval)": "(قابلة للتعديل قبل الاعتماد)", "(locked)": "(مقفلة)",
    "Subject": "الموضوع", "Summary": "الملخص", "Evidence summary": "ملخص الأدلة", "Requested review": "المراجعة المطلوبة",
    "Additional notes": "ملاحظات إضافية", "Save draft": "حفظ المسودة",
    "No draft yet. Set a reason, then generate one.": "لا توجد مسودة بعد. عيّن السبب ثم ولّد المسودة.",
    "Evidence": "الأدلة", "Verify integrity": "التحقق من السلامة", "Integrity": "السلامة", "Chain of custody": "سلسلة الحيازة",
    "Intact": "سليم", "Unverified": "لم يُتحقَّق", "Mismatch": "غير مطابق", "No evidence attached yet.": "لم تُرفق أدلة بعد.",
    "Link, reference or hash": "رابط أو مرجع أو بصمة", "Add evidence": "إضافة دليل",
    "Official submissions": "التسليمات الرسمية", "Attempts": "المحاولات", "Responses": "الردود",
    "Approve and run": "اعتماد وتنفيذ", "Reference number": "الرقم المرجعي", "Confirm sent": "تأكيد الإرسال",
    "Response text": "نص الرد", "Record response": "تسجيل الرد", "Cancel": "إلغاء", "No submissions yet.": "لا توجد تسليمات بعد.",
    "Channel": "القناة", "Official recipient or portal": "المستلم الرسمي أو البوابة", "Create submission": "إنشاء تسليم",
    "Run it now (explicit approval, recorded in the audit log)": "نفّذه الآن (اعتماد صريح يُسجَّل في سجل التدقيق)",
    "Details": "التفاصيل", "Assigned to": "مُسند إلى", "Created": "أُنشئت", "Approved by": "اعتمدها", "History": "السجل الزمني",
    "Case is now: {status}": "حالة القضية الآن: {status}", "Reason updated": "حُدّث السبب", "Draft generated": "وُلّدت المسودة",
    "Draft saved": "حُفظت المسودة", "PDF exported: {file}": "صُدّر ملف PDF: {file}",
    "Evidence package created: {file}": "أُنشئت حزمة الأدلة: {file}",
    "Evidence verified: {ok} of {total} intact": "تم التحقق من الأدلة: {ok} من {total} سليمة",
    "Evidence added": "أُضيف الدليل", "Submission {id} created": "أُنشئ التسليم {id}",
    "Submission {id} created and run → {status}": "أُنشئ التسليم {id} ونُفّذ: {status}",
    # submissions
    "Submissions and responses": "التسليمات والردود",
    "Official channels only: manual, official portal, or an allow-listed official email. No account-based submission exists.":
        "قنوات رسمية فقط: يدويًا، أو عبر بوابة رسمية، أو بريد رسمي من قائمة معتمدة. لا يوجد أي تسليم عبر الحسابات.",
    "Case": "القضية", "Recipient": "المستلم", "Result": "النتيجة", "Submission run → {status}": "نُفّذ التسليم: {status}",
    "Confirmed as sent": "تم تأكيد الإرسال", "Response recorded": "سُجّل الرد", "Submission cancelled": "أُلغي التسليم",
    # errors / audit
    "Categorised failures across sessions, network, submissions and the database.": "الإخفاقات مصنّفة عبر الجلسات والشبكة والتسليمات وقاعدة البيانات.",
    "All": "الكل", "When": "الوقت", "Category": "الفئة", "Component": "المكوّن", "Session": "الجلسة", "Provider": "المزوّد",
    "Message": "الرسالة", "No errors recorded.": "لا توجد أخطاء مسجّلة.",
    "Audit log": "سجل التدقيق", "Who did what, when, on which session or case, and with what result.":
        "من فعل ماذا، ومتى، وعلى أي جلسة أو قضية، وبأي نتيجة.",
    "Action": "الإجراء", "Any action": "كل الإجراءات", "Actor": "المنفّذ", "Case ID": "معرّف القضية", "Entity": "الكيان",
    "No audit entries.": "لا توجد سجلات تدقيق.",
    # reports
    "Reports and analytics": "التقارير والتحليلات",
    "Read-only insight into processing time, outcomes and failure causes.": "رؤية للقراءة فقط لزمن المعالجة والنتائج وأسباب الفشل.",
    "Average processing (hours)": "متوسط المعالجة (ساعات)", "Average response (hours)": "متوسط الرد (ساعات)",
    "Session availability": "توافر الجلسات", "Submissions completed / failed": "التسليمات المكتملة / الفاشلة",
    "Submissions per day (last 30 days)": "التسليمات يوميًا (آخر 30 يومًا)", "No submissions in the last 30 days.": "لا توجد تسليمات في آخر 30 يومًا.",
    "By reason": "حسب السبب", "Failure causes (30 days)": "أسباب الفشل (30 يومًا)", "No failures recorded.": "لا توجد إخفاقات مسجّلة.",
    "Submission outcomes": "نتائج التسليمات", "Export": "تصدير", "Dataset": "مجموعة البيانات", "Format": "الصيغة", "By": "بواسطة",
    "No exports yet.": "لا توجد عمليات تصدير بعد.", "Export created: {file}": "أُنشئ التصدير: {file}",
    # settings
    "Settings and monitoring": "الإعدادات والمراقبة",
    "System health, capacity model, backups, reason catalog and access control.": "صحة النظام ونموذج السعة والنسخ الاحتياطي وكتالوج الأسباب والصلاحيات.",
    "CPU %": "المعالج %", "Memory %": "الذاكرة %", "Process memory (MB)": "ذاكرة العملية (MB)", "Free disk (GB)": "المساحة الحرة (GB)",
    "Database ping (ms)": "زمن استجابة قاعدة البيانات (ms)", "Uptime (s)": "مدة التشغيل (ث)", "Queue and workers": "الطابور والعمّال",
    "Capacity model": "نموذج السعة", "Activity (24 hours)": "النشاط (24 ساعة)", "Session checks": "فحوص الجلسات",
    "{n} succeeded": "{n} ناجحة", "Environment": "البيئة", "Mode": "الوضع", "Validation provider": "مزوّد الفحص",
    "Database": "قاعدة البيانات", "Official email (SMTP)": "البريد الرسمي (SMTP)", "On": "مفعّل", "Off": "معطّل",
    "Session encryption": "تشفير الجلسات", "Backups": "النسخ الاحتياطية", "restored": "مُستعادة", "No backups yet.": "لا توجد نسخ احتياطية بعد.",
    "Reason catalog": "كتالوج الأسباب", "Code": "الرمز", "Policy": "السياسة", "Official channel": "القناة الرسمية", "Version": "الإصدار",
    "Users and roles": "المستخدمون والأدوار", "Role": "الدور", "Active": "نشط", "Last sign-in": "آخر دخول", "Yes": "نعم", "No": "لا",
    "Your permissions": "صلاحياتك", "About": "حول المنصة", "Open-source components": "مكوّنات مفتوحة المصدر",
    # generic messages
    "Error: {message}": "خطأ: {message}", "Unknown action": "إجراء غير معروف",
    "Permission '{perm}' is required": "هذا الإجراء يتطلب الصلاحية «{perm}»",
    "Backup created: {file}": "أُنشئت النسخة الاحتياطية: {file}",
    # session upload
    "Upload session files": "رفع ملفات الجلسات",
    "Drop .session files or a .zip here, or click to choose": "اسحب ملفات ‎.session‎ أو ملف ‎.zip‎ إلى هنا، أو اضغط للاختيار",
    "One file per account (Telethon or Pyrogram), up to {mb} MB each. Every file is checked before it is saved to sessions/active; duplicates are skipped.":
        "ملف واحد لكل حساب (Telethon أو Pyrogram)، حتى {mb} ميغابايت للملف. يُفحص كل ملف قبل حفظه في sessions/active، والملفات المكرّرة تُتجاهل.",
    "Run a health check after upload": "افحص صحتها بعد الرفع",
    "Upload": "رفع", "Uploading…": "جارٍ الرفع…", "and {n} more": "و{n} ملفات أخرى",
    "Choose .session files or a .zip to upload": "اختر ملفات ‎.session‎ أو ملف ‎.zip‎ للرفع",
    "Upload finished: {added} added, {dup} already registered, {bad} rejected":
        "انتهى الرفع: أُضيفت {added}، و{dup} مسجّلة من قبل، ورُفض {bad}",
    "No sessions yet. Upload .session files above, or put them under sessions/active and scan.":
        "لا توجد جلسات بعد. ارفع ملفات ‎.session‎ من الأعلى، أو ضعها في المجلد sessions/active ثم اضغط «فحص المجلد».",
    "only .session files or a .zip of them are accepted": "تُقبل ملفات ‎.session‎ أو ملف ‎.zip‎ يحتويها فقط",
    "file is too large": "الملف كبير جدًا",
    "not a Telethon or Pyrogram session file": "ليس ملف جلسة Telethon أو Pyrogram",
    "not a readable ZIP archive": "ملف ZIP تالف أو محمي بكلمة مرور",
    "the ZIP contains no .session files": "ملف ZIP لا يحتوي على ملفات ‎.session‎",
    # check mode (validation provider)
    "Check mode": "وضع الفحص", "Real (Telethon)": "حقيقي (Telethon)", "Simulation": "محاكاة",
    "ready": "جاهز", "not ready": "غير جاهز",
    "Checks are real: each account is connected only to confirm it is authorised, then disconnected.":
        "الفحص حقيقي: يتصل بكل حساب فقط للتأكد أنه يعمل، ثم يقطع الاتصال.",
    "Real checks are switched on but cannot run yet:": "الفحص الحقيقي مفعّل لكنه لا يستطيع العمل بعد:",
    "The telethon library is not installed. Run the launcher again (run_windows.bat); it installs it automatically.":
        "مكتبة telethon غير مثبّتة. أغلق النافذة وشغّل المشغّل من جديد (run_windows.bat) وسيثبّتها تلقائيًا.",
    "Add TELEGRAM_API_ID and TELEGRAM_API_HASH to the .env file, then restart.":
        "أضف TELEGRAM_API_ID و TELEGRAM_API_HASH إلى ملف ‎.env‎ ثم أعد التشغيل.",
    "Results are simulated for testing and do not come from Telegram.": "النتائج تجريبية للاختبار، وليست من تيليجرام.",
    "Your API ID and API hash are in .env, but this line still says simulation. Change it to:":
        "وجدنا API ID و API hash في ملف ‎.env‎، لكن هذا السطر ما زال على simulation. غيّره إلى:",
    "Put these lines in the .env file:": "ضع هذه الأسطر في ملف ‎.env‎:",
    "Then close the window and run the launcher again (run_windows.bat).": "ثم أغلق النافذة وشغّل المشغّل من جديد (run_windows.bat).",
    # official submission guidance
    "Official email: sent once to the official Telegram address you choose, with the file evidence attached.":
        "البريد الرسمي: يُرسل البلاغ مرة واحدة إلى عنوان تيليجرام الرسمي الذي تختاره، مع ملفات الأدلة مرفقة.",
    "Email is not set up yet, so an .eml file is created in the exports folder for you to send from your own mailbox.":
        "البريد غير مضبوط بعد، لذلك يُنشأ ملف ‎.eml‎ في مجلد exports لترسله من بريدك.",
    "Manual / official portal: you file the report yourself with the report button in the Telegram app or at telegram.org/support, then confirm it here with the reference.":
        "يدوي أو بوابة رسمية: تقدّم البلاغ بنفسك من زر الإبلاغ في تطبيق تيليجرام أو من telegram.org/support، ثم تؤكّده هنا بالرقم المرجعي.",
    "Telegram usually does not answer each report individually. If the target is removed or restricted (its link shows it is unavailable), record the response as Accepted; the case then moves to Completed.":
        "تيليجرام لا يرد غالبًا على كل بلاغ بشكل منفصل. إذا حُذف الهدف أو قُيّد (يظهر رابطه غير متاح)، سجّل الرد «مقبول» فتنتقل القضية إلى «مكتمل».",
}
