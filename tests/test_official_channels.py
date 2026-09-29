"""The official-reporting catalog: a complete, ready-made list of Telegram's documented channels, the right one
picked automatically from the case's reason, and extension from settings without editing code."""
from __future__ import annotations

from app.config import get_settings
from app.domain.enums import SubmissionChannel
from app.services import submissions as ss
from app.submission import channels as ch
from app.submission.channels import official_channels, official_email_recipients, official_portals
from tests.test_case_workflow import _ready_case


def test_base_catalog_covers_the_documented_channels():
    keys = {c.key for c in official_channels()}
    assert {"abuse@telegram.org", "dmca@telegram.org", "stopca@telegram.org", "sticker-abuse@telegram.org"} <= keys
    assert {"in_app_report", "@isiswatch", "@notoscam", "telegram_support", "dsa_report"} <= keys
    kinds = {c.key: c.kind for c in official_channels()}
    assert kinds["abuse@telegram.org"] == "email" and kinds["@isiswatch"] == "portal"
    # every channel carries an English and an Arabic label, so the picker is bilingual
    assert all(c.label_en and c.label_ar for c in official_channels())


def test_catalog_can_be_extended_from_settings_without_code(settings):
    extra = [{"key": "LEGAL@example.GOV", "kind": "email", "label": "National regulator"},
             {"key": "national_portal", "kind": "portal", "label": "National portal", "how": "https://report.example.gov"},
             {"bad": "ignored"}, {"key": "x", "kind": "nonsense"}]
    s = settings.model_copy(update={"official_channels_extra": extra})
    emails, portals = official_email_recipients(s), official_portals(s)
    assert emails["legal@example.gov"] == "National regulator"       # normalised to lower-case, allow-listed
    assert portals["national_portal"] == "https://report.example.gov"
    assert "x" not in emails and "x" not in portals                  # malformed rows are dropped


async def test_email_channel_accepts_a_settings_extra_recipient(db, seeded, settings, monkeypatch):
    c = await _ready_case(db)
    monkeypatch.setattr(get_settings(), "official_channels_extra",
                        [{"key": "legal@example.gov", "kind": "email", "label": "Regulator"}])
    sub = await ss.create_submission(db, c, channel="official_email", recipient="legal@example.gov", actor="op")
    await ss.execute_submission(db, sub, approved_by="reviewer")
    assert sub.status.upper() in ("WAITING", "COMPLETED") and not sub.error_message


async def test_new_portals_are_accepted(db, seeded):
    c = await _ready_case(db)
    sub = await ss.create_submission(db, c, channel="official_portal", recipient="@ISISwatch", actor="op")
    await ss.execute_submission(db, sub, approved_by="op")
    assert sub.status == "WAITING" and "isiswatch" in (sub.result or "").lower()


# ----------------------------------------------------------------------------------------------- auto-fill
async def test_recipient_defaults_from_the_reason_without_typing(db, seeded):
    """A copyright case, submitted by email with no recipient, must go to dmca@ automatically."""
    from app.services import cases as cs
    from app.services import evidence as ev
    from app.services.targets import create_target

    t = await create_target(db, target_type="channel", url="https://t.me/pirate", title="Pirate", actor="op")
    c = await cs.create_case(db, target_id=t.id, title="Copyright", reason_code="copyright", actor="op")
    await ev.add_reference_evidence(db, c, evidence_type="url", title="p", value="https://t.me/pirate/1", actor="op")
    await cs.generate_draft(db, c, actor="op")
    await cs.submit_for_review(db, c, actor="op")
    await cs.approve(db, c, actor="reviewer")
    await cs.mark_ready(db, c, actor="op")
    sub = await ss.create_submission(db, c, channel="official_email", recipient=None, actor="op")
    assert sub.recipient == "dmca@telegram.org"


async def test_default_recipient_matches_the_channel_kind(db, seeded):
    c = await _ready_case(db)  # reason illegal_content → email hint abuse@telegram.org
    assert await ss.default_recipient(db, c, SubmissionChannel.OFFICIAL_EMAIL) == "abuse@telegram.org"
    # the same reason on a portal falls back to a portal default, never the email hint
    assert await ss.default_recipient(db, c, SubmissionChannel.OFFICIAL_PORTAL) == "in_app_report"


async def test_spam_reason_defaults_to_the_in_app_report(db, seeded):
    from app.services import cases as cs
    from app.services.targets import create_target

    t = await create_target(db, target_type="account", username="spammer", actor="op")
    c = await cs.create_case(db, target_id=t.id, title="Spam", reason_code="spam", actor="op")
    assert await ss.default_recipient(db, c, SubmissionChannel.OFFICIAL_PORTAL) == "in_app_report"


# ----------------------------------------------------------------------------------------------- UI + API
async def test_case_page_lists_all_official_channels_grouped(client, db, seeded):
    r = await client.post("/login", data={"username": "admin", "password": "admin-pass-123", "next": "/"})
    assert r.status_code == 303
    c = await _ready_case(db)
    await db.commit()
    html = (await client.get(f"/cases/{c.id}")).text
    assert "عناوين البريد الرسمية" in html and "البوابات والقنوات الرسمية" in html
    for key in ("abuse@telegram.org", "dmca@telegram.org", "stopca@telegram.org", "@isiswatch", "@notoscam", "dsa_report"):
        assert key in html, key
    assert 'data-default-email="abuse@telegram.org"' in html  # pre-selected from the reason


async def test_channels_api_returns_the_full_catalog(client):
    op = await login_op(client)
    body = (await client.get("/api/v1/submissions/channels", headers=op)).json()
    keys = {c["key"] for c in body["catalog"]}
    assert "abuse@telegram.org" in keys and "@isiswatch" in keys
    assert "dmca@telegram.org" in body["official_email"] and "dsa_report" in body["official_portal"]


async def login_op(client):
    from tests.conftest import login

    return await login(client, "operator")
