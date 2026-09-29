"""Bilingual (Arabic default / English), themed (dark default / light) dashboard: behaviour, safety and completeness."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.domain import enums
from app.web import charts
from app.web.i18n import AR, AR_LABELS, AR_REASONS, STATUS_TONES, label, translator

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "web" / "templates"
PAGES = ("/", "/sessions", "/accounts", "/proxies", "/targets", "/cases", "/submissions", "/errors", "/audit",
         "/reports", "/settings")


async def _login_admin(client):
    r = await client.post("/login", data={"username": "admin", "password": "admin-pass-123", "next": "/"})
    assert r.status_code == 303


# ----------------------------------------------------------------------------------------------- language
async def test_arabic_is_the_default_and_rtl(client):
    html = (await client.get("/login")).text
    assert '<html lang="ar" dir="rtl" data-theme="dark">' in html
    assert "تسجيل الدخول" in html and "Sign in" not in html


async def test_language_switch_sets_cookie_and_renders_english(client):
    r = await client.get("/lang/en", params={"next": "/login"})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "lang=en" in r.headers["set-cookie"] and "samesite=lax" in r.headers["set-cookie"].lower()
    html = (await client.get("/login")).text
    assert '<html lang="en" dir="ltr"' in html and "Sign in" in html
    r = await client.get("/lang/ar", params={"next": "/login"})
    assert "lang=ar" in r.headers["set-cookie"]
    assert 'dir="rtl"' in (await client.get("/login")).text


async def test_unknown_language_and_theme_fall_back_safely(client):
    r = await client.get("/lang/xx", params={"next": "/login"})
    assert "lang=ar" in r.headers["set-cookie"]
    r = await client.get("/theme/neon", params={"next": "/login"})
    assert "theme=dark" in r.headers["set-cookie"]


@pytest.mark.parametrize("bad", ["//evil.example/x", "https://evil.example", "/\\evil.example", "", "evil"])
async def test_preference_redirects_cannot_leave_the_site(client, bad):
    for route in ("/lang/en", "/theme/light"):
        r = await client.get(route, params={"next": bad})
        assert r.status_code == 303 and r.headers["location"] == "/"


async def test_login_next_parameter_cannot_leave_the_site(client):
    r = await client.post("/login", data={"username": "admin", "password": "admin-pass-123", "next": "//evil.example"})
    assert r.status_code == 303 and r.headers["location"] == "/"


# ----------------------------------------------------------------------------------------------- theme
async def test_theme_switch_sets_cookie_and_renders_light(client):
    r = await client.get("/theme/light", params={"next": "/login"})
    assert r.status_code == 303 and "theme=light" in r.headers["set-cookie"]
    assert 'data-theme="light"' in (await client.get("/login")).text


@pytest.mark.parametrize("lang,theme", [("ar", "dark"), ("ar", "light"), ("en", "dark"), ("en", "light")])
async def test_every_page_renders_in_every_language_and_theme(client, lang, theme):
    await _login_admin(client)
    client.cookies.set("lang", lang)
    client.cookies.set("theme", theme)
    for path in PAGES:
        r = await client.get(path)
        assert r.status_code == 200, (path, lang, theme)
        assert f'lang="{lang}"' in r.text and f'data-theme="{theme}"' in r.text
    r = await client.get("/sessions", headers={"HX-Request": "true"})
    assert "<table" in r.text and "<html" not in r.text


# ----------------------------------------------------------------------------------------------- copyright
async def test_copyright_on_login_and_settings_only(client):
    login_ar = (await client.get("/login")).text
    assert "مرتضى أبو زينب" in login_ar and "جميع الحقوق محفوظة" in login_ar
    await _login_admin(client)
    assert "مرتضى أبو زينب" in (await client.get("/settings")).text
    for path in ("/", "/sessions", "/cases"):
        assert "مرتضى أبو زينب" not in (await client.get(path)).text, path
    client.cookies.set("lang", "en")
    assert "Murtada Abu Zainab" in (await client.get("/settings")).text
    assert "All rights reserved" in (await client.get("/settings")).text


# ----------------------------------------------------------------------------------------------- flash cookie
async def test_arabic_and_arrow_flash_messages_do_not_crash(client):
    """Flash text is percent-encoded: Arabic or "→" in a latin-1 Set-Cookie header used to raise."""
    await _login_admin(client)
    r = await client.post("/proxies", data={"host": "127.0.0.1", "port": "1", "protocol": "socks5"})
    assert r.status_code == 303
    page = await client.get("/proxies")
    assert "أُضيف البروكسي" in page.text
    pid = (await client.get("/api/v1/proxies")).json()[0]["id"]
    client.cookies.set("lang", "en")
    r = await client.post(f"/proxies/{pid}/check")
    assert r.status_code == 303
    page = await client.get("/proxies")
    assert "127.0.0.1:1 → Down" in page.text


async def test_permission_denied_flash_is_translated(client):
    r = await client.post("/login", data={"username": "viewer", "password": "viewer-pass-123", "next": "/"})
    assert r.status_code == 303
    r = await client.post("/sessions/scan")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "هذا الإجراء يتطلب الصلاحية" in (await client.get("/")).text


async def test_failed_login_message_is_translated(client):
    r = await client.post("/login", data={"username": "admin", "password": "wrong", "next": "/"})
    assert r.status_code == 200 and "اسم المستخدم أو كلمة المرور غير صحيحة" in r.text


# ----------------------------------------------------------------------------------------------- completeness
def _template_keys() -> set[str]:
    keys: set[str] = set()
    for p in TEMPLATES.rglob("*.html"):
        s = p.read_text(encoding="utf-8")
        keys.update(re.findall(r"_\(\s*'((?:[^'\\]|\\.)*)'\s*\)", s))
        keys.update(re.findall(r'_\(\s*"((?:[^"\\]|\\.)*)"\s*\)', s))
    router = (ROOT / "app" / "web" / "router.py").read_text(encoding="utf-8")
    for rx in (r'\bt\(\s*"((?:[^"\\]|\\.)*)"', r'Tr\(request\)\(\s*"((?:[^"\\]|\\.)*)"', r'self\(\s*"((?:[^"\\]|\\.)*)"'):
        keys.update(re.findall(rx, router))
    return keys


def test_every_ui_string_has_an_arabic_translation():
    keys = _template_keys()
    assert len(keys) > 200
    missing = sorted(k for k in keys if k not in AR)
    assert not missing, missing


def test_translations_keep_their_placeholders():
    for key, value in AR.items():
        assert set(re.findall(r"\{(\w+)\}", key)) == set(re.findall(r"\{(\w+)\}", value)), key


@pytest.mark.parametrize("enum_cls", [enums.SessionStatus, enums.SessionLocation, enums.HealthState, enums.ProxyStatus,
                                      enums.ProxyProtocol, enums.TargetType, enums.TargetStatus, enums.CaseStatus,
                                      enums.CasePriority, enums.EvidenceType, enums.SubmissionChannel,
                                      enums.ExecutionStatus, enums.ErrorCategory, enums.AuditAction, enums.Role])
def test_every_enum_value_has_an_arabic_label(enum_cls):
    missing = [e.value for e in enum_cls if e.value not in AR_LABELS]
    assert not missing, missing


def test_seeded_reasons_have_arabic_names():
    import json

    seed = json.loads((ROOT / "app" / "data" / "reasons_seed.json").read_text(encoding="utf-8"))
    assert {r["code"] for r in seed} <= set(AR_REASONS)


def test_label_and_translator_fallbacks():
    assert label("ar", "VALID") == "صالح" and label("en", "CHECK_FAILED") == "Check failed"
    assert label("en", "channel") == "Channel" and label("ar", None) == "—"
    assert translator("ar")("Sessions") == "الجلسات" and translator("en")("Sessions") == "Sessions"
    assert translator("ar")("some brand-new string") == "some brand-new string"
    assert STATUS_TONES["BANNED"] == "bad" and STATUS_TONES["Healthy"] == "ok"


# ----------------------------------------------------------------------------------------------- assets
async def test_login_3d_scene_and_fonts_are_self_hosted(client):
    html = (await client.get("/login")).text
    refs = re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', html)
    assert "/static/login3d.js" in refs and all(r.startswith("/static/") for r in refs)
    for path in ("/static/login3d.js", "/static/ui.js", "/static/vendor/three.module.min.js", "/static/favicon.svg",
                 "/static/fonts/ibm-plex-sans-arabic-arabic-400-normal.woff2",
                 "/static/fonts/ibm-plex-sans-arabic-latin-700-normal.woff2"):
        r = await client.get(path)
        assert r.status_code == 200 and len(r.content) > 300, path
    js = (await client.get("/static/login3d.js")).text
    assert 'from "./vendor/three.module.min.js"' in js and "http" not in js.split("import", 1)[1].split("\n", 1)[0]
    css = (await client.get("/static/app.css")).text
    assert "fonts.googleapis" not in css and "/static/fonts/ibm-plex-sans-arabic-arabic-400-normal.woff2" in css


# ----------------------------------------------------------------------------------------------- charts
def test_donut_geometry_gaps_and_totals():
    d = charts.donut([("a", "#111", 3), ("b", "#222", 1), ("c", "#333", 0)])
    assert d["total"] == 4 and len(d["segments"]) == 2 and len(d["legend"]) == 3
    circ = d["circumference"]
    a, b = d["segments"]
    assert abs((a["dash"] + 2) - circ * 0.75) < 0.01 and abs((b["dash"] + 2) - circ * 0.25) < 0.01
    assert a["offset"] == 0 and abs(b["offset"] + circ * 0.75) < 0.01
    assert [l["pct"] for l in d["legend"]] == [75, 25, 0]
    single = charts.donut([("only", "#000", 5), ("none", "#fff", 0)])
    assert single["segments"][0]["dash"] == pytest.approx(single["circumference"], abs=0.01)  # closed ring, no gap
    assert charts.donut([("x", "#000", 0)])["segments"] == []


def test_bars_scale_to_the_maximum():
    rows = charts.bars([("a", 10), ("b", 5), ("c", 0)])
    assert [r["pct"] for r in rows] == [100.0, 50.0, 0.0]
    assert charts.bars([("a", 0)])[0]["pct"] == 0.0 and charts.bars([]) == []


def test_daily_series_is_a_continuous_window():
    """One active day must not render as a single full-width bar: every day of the window gets a column."""
    from datetime import date

    s = charts.daily_series([("2026-09-27", 2), ("2026-09-29 00:00:00", 5), ("2026-08-01", 9)], days=5,
                            today=date(2026, 9, 29))
    assert s == [("2026-09-25", 0), ("2026-09-26", 0), ("2026-09-27", 2), ("2026-09-28", 0), ("2026-09-29", 5)]
    assert len(charts.daily_series([], today=date(2026, 9, 29))) == 30
