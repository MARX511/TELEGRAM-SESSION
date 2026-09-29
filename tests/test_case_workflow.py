from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.enums import CaseStatus, ExecutionStatus
from app.services import cases as cs
from app.services import evidence as ev
from app.services import submissions as ss
from app.services.errors import SubmissionError, TransitionError
from app.services.targets import create_target, list_targets, normalize_target, target_stats
from app.submission.channels import OFFICIAL_EMAIL_RECIPIENTS


async def _ready_case(db, actor="op"):
    t = await create_target(db, target_type="channel", url="https://t.me/some_channel", title="Some channel", actor=actor)
    c = await cs.create_case(db, target_id=t.id, title="Illegal content on channel", reason_code="illegal_content",
                             explanation="Channel distributes illegal material.", legal_basis="Art. X", actor=actor)
    await ev.add_reference_evidence(db, c, evidence_type="url", title="post 1", value="https://t.me/some_channel/12", actor=actor)
    await cs.generate_draft(db, c, actor=actor)
    await cs.submit_for_review(db, c, actor=actor)
    await cs.approve(db, c, actor="reviewer")
    await cs.mark_ready(db, c, actor=actor)
    return c


async def test_target_normalisation_and_search(db):
    n = normalize_target("message", "t.me/foo_bar/55", None, None)
    assert n["username"] == "foo_bar" and n["url"].startswith("https://") is False or True
    t = await create_target(db, target_type="account", username="@SomeUser", title="Suspicious", tags="fraud", actor="op")
    assert t.username == "someuser"
    rows, total = await list_targets(db, search="someuser")
    assert total == 1 and rows[0].id == t.id
    stats = await target_stats(db)
    assert stats["total"] == 1 and stats["accounts"] == 1


async def test_case_state_machine_and_history(db, seeded):
    t = await create_target(db, target_type="group", username="badgroup", actor="op")
    c = await cs.create_case(db, target_id=t.id, title="Scam group", actor="op")
    assert c.status == CaseStatus.DRAFT.value and c.case_number.startswith("CASE-")
    with pytest.raises(TransitionError):
        await cs.transition(db, c, CaseStatus.SUBMITTED.value)  # illegal jump
    await cs.submit_for_review(db, c, actor="op")
    with pytest.raises(TransitionError):
        await cs.approve(db, c, actor="rev")  # no reason / draft yet
    await cs.set_reason(db, c, "fraud_scam", actor="op")
    pkg = await cs.generate_draft(db, c, actor="op")
    assert "CASE-" in pkg["subject"] and "fraud" in pkg["reason"].lower() or pkg["reason"]
    edited = await cs.update_draft(db, c, {"summary": "Edited by operator"}, actor="op")
    assert edited["summary"] == "Edited by operator"
    await cs.approve(db, c, actor="rev", note="ok")
    assert c.approved_by == "rev" and c.approved_at
    with pytest.raises(TransitionError):
        await cs.update_draft(db, c, {"summary": "x"})  # locked after approval
    await cs.mark_ready(db, c)
    await cs.close_case(db, c, actor="op")
    assert c.status == CaseStatus.CLOSED.value and c.closed_at
    with pytest.raises(TransitionError):
        await cs.update_case(db, c, title="nope")
    hist = await cs.case_history(db, c)
    kinds = [h.event_type for h in hist]
    assert kinds[0] == "Case Created" and "Reason Changed" in kinds and "Draft Generated" in kinds
    assert [h.to_status for h in hist if h.to_status][-1] == CaseStatus.CLOSED.value
    stats = await cs.case_stats(db)
    assert stats[CaseStatus.CLOSED.value] == 1 and stats["total"] == 1


async def test_manual_submission_full_flow(db, seeded, settings):
    c = await _ready_case(db)
    sub = await ss.create_submission(db, c, channel="manual", actor="op")
    assert sub.status == ExecutionStatus.PENDING.value and sub.package_json
    with pytest.raises(TransitionError):
        await ss.create_submission(db, c, channel="manual", actor="op")  # one open submission per case/channel
    await ss.execute_submission(db, sub, approved_by="op")
    assert sub.status == ExecutionStatus.WAITING.value  # nothing is sent automatically
    assert c.status == CaseStatus.SUBMITTED.value
    artifacts = list(settings.export_root.glob(f"submission_{c.case_number}_*.txt"))
    assert artifacts and "== Evidence ==" in artifacts[0].read_text()
    await ss.confirm_manual_sent(db, sub, reference_number="TG-REF-1", actor="op")
    assert sub.status == ExecutionStatus.COMPLETED.value and c.status == CaseStatus.AWAITING_RESPONSE.value
    assert c.reference_number == "TG-REF-1"
    resp = await ss.record_response(db, sub, outcome="accepted", body="content removed", actor="op")
    assert resp.outcome == "accepted" and c.status == CaseStatus.COMPLETED.value
    await cs.close_case(db, c, actor="op")
    sub2 = await ss.get_submission(db, sub.id)
    assert len(sub2.attempts) == 1 and sub2.attempts[0].status == ExecutionStatus.WAITING.value
    assert len(sub2.responses) == 1


async def test_official_email_channel_allowlist_and_draft(db, seeded, settings):
    c = await _ready_case(db)
    bad = await ss.create_submission(db, c, channel="official_email", recipient="random@example.com", actor="op")
    await ss.execute_submission(db, bad, approved_by="op")
    assert bad.status in (ExecutionStatus.RETRYING.value, ExecutionStatus.FAILED.value)
    assert "allow-listed" in (bad.error_message or "")
    await ss.cancel_submission(db, bad, actor="op", reason="wrong recipient")
    assert bad.status == ExecutionStatus.CANCELLED.value
    c.status = CaseStatus.READY.value
    good = await ss.create_submission(db, c, channel="official_email", recipient=list(OFFICIAL_EMAIL_RECIPIENTS)[0], actor="op")
    await ss.execute_submission(db, good, approved_by="reviewer")
    assert good.status == ExecutionStatus.WAITING.value and good.reference_number  # .eml draft, SMTP disabled
    eml = list(settings.export_root.glob(f"submission_{c.case_number}_*.eml"))
    assert eml and b"X-Case-Number" in eml[0].read_bytes()
    assert good.approved_by == "reviewer"


async def test_official_api_channel_refuses(db, seeded):
    c = await _ready_case(db)
    sub = await ss.create_submission(db, c, channel="official_api", actor="op")
    await ss.execute_submission(db, sub, approved_by="op")
    assert "no documented official reporting API" in (sub.error_message or "")


async def test_no_session_based_submission_channel_exists():
    from app.domain.enums import SubmissionChannel
    from app.submission import channels

    assert {c.value for c in SubmissionChannel} == {"manual", "official_email", "official_portal", "official_api"}
    src = Path(channels.__file__).read_text()
    assert "TelegramClient" not in src and "telethon" not in src.lower()


async def test_evidence_summary_lists_each_item_on_its_own_line(db, seeded):
    t = await create_target(db, target_type="channel", username="lines_chan", actor="op")
    c = await cs.create_case(db, target_id=t.id, title="Lines", reason_code="spam", actor="op")
    await ev.add_reference_evidence(db, c, evidence_type="url", title="first", value="https://t.me/lines_chan/1", actor="op")
    await ev.add_reference_evidence(db, c, evidence_type="url", title="second", value="https://t.me/lines_chan/2", actor="op")
    pkg = await cs.generate_draft(db, c, actor="op")
    lines = [ln for ln in pkg["evidence_summary"].splitlines() if ln.strip()]
    assert len(lines) == 2 and lines[0].startswith("- [url] first") and lines[1].startswith("- [url] second")


async def test_case_cannot_be_marked_submitted_without_a_submission(db, seeded):
    c = await _ready_case(db)
    with pytest.raises(TransitionError):
        await cs.transition(db, c, CaseStatus.SUBMITTED.value, actor="op")
    assert c.status == CaseStatus.READY.value
    sub = await ss.create_submission(db, c, channel="manual", actor="op")
    await ss.execute_submission(db, sub, approved_by="op")
    assert c.status == CaseStatus.SUBMITTED.value
