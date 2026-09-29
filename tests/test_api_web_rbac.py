from __future__ import annotations

from tests.conftest import login


async def test_auth_and_rbac(client):
    assert (await client.get("/api/v1/sessions")).status_code == 401
    viewer = await login(client, "viewer")
    assert (await client.get("/api/v1/auth/me", headers=viewer)).json()["role"] == "viewer"
    r = await client.post("/api/v1/targets", json={"target_type": "channel", "username": "x"}, headers=viewer)
    assert r.status_code == 403
    operator = await login(client, "operator")
    r = await client.post("/api/v1/targets", json={"target_type": "channel", "username": "x", "title": "X"}, headers=operator)
    assert r.status_code == 201, r.text
    target = r.json()
    r = await client.post("/api/v1/cases", json={"target_id": target["id"], "title": "Case", "reason_code": "spam"}, headers=operator)
    assert r.status_code == 201
    case = r.json()
    assert (await client.post(f"/api/v1/cases/{case['id']}/draft", headers=operator)).status_code == 200
    assert (await client.post(f"/api/v1/cases/{case['id']}/transition", json={"to_status": "Pending Review"}, headers=operator)).status_code == 200
    # operator cannot approve; reviewer can
    assert (await client.post(f"/api/v1/cases/{case['id']}/approve", headers=operator)).status_code == 403
    reviewer = await login(client, "reviewer")
    assert (await client.post(f"/api/v1/cases/{case['id']}/approve", headers=reviewer)).status_code == 200
    assert (await client.post(f"/api/v1/cases/{case['id']}/transition", json={"to_status": "Ready"}, headers=operator)).status_code == 200
    r = await client.post(f"/api/v1/submissions/cases/{case['id']}", json={"channel": "manual"}, headers=operator)
    assert r.status_code == 201 and r.json()["status"] == "PENDING"
    sub = r.json()
    r = await client.post(f"/api/v1/submissions/{sub['id']}/execute", headers=operator)
    assert r.status_code == 200 and r.json()["status"] == "WAITING"
    r = await client.post(f"/api/v1/submissions/{sub['id']}/confirm", json={"reference_number": "REF-9"}, headers=operator)
    assert r.json()["status"] == "COMPLETED"
    # audit visible to auditor, not to viewer
    assert (await client.get("/api/v1/audit", headers=viewer)).status_code == 403
    auditor = await login(client, "auditor")
    r = await client.get("/api/v1/audit", headers=auditor, params={"action": "Login"})
    assert r.status_code == 200 and r.json()["total"] >= 4
    r = await client.get("/api/v1/audit", headers=auditor, params={"case_id": case["id"]})
    actions = {i["action"] for i in r.json()["items"]}
    assert {"Case Created", "Draft Generated", "Approval Granted", "Submission Created", "Submission Sent"} <= actions
    # bad login is audited
    assert (await client.post("/api/v1/auth/login", json={"username": "viewer", "password": "wrong"})).status_code == 401
    r = await client.get("/api/v1/audit", headers=auditor, params={"action": "Login Failed"})
    assert r.json()["total"] == 1


async def test_sessions_api_and_monitoring(client, make_session_file):
    for n in ("api_a.session", "api_b.session", "api_banned.session"):
        make_session_file(n)
    op = await login(client, "operator")
    r = await client.post("/api/v1/sessions/scan", headers=op)
    assert r.json()["new"] == 3
    r = await client.post("/api/v1/sessions/check", json={"inline": True}, headers=op)
    assert r.status_code == 200 and r.json()["checked"] == 3 and r.json()["succeeded"] == 2
    r = await client.get("/api/v1/sessions", headers=op, params={"status": "VALID"})
    assert r.json()["total"] == 2
    stats = (await client.get("/api/v1/sessions/stats", headers=op)).json()
    assert stats["valid"] == 2 and stats["banned"] == 1
    sid = r.json()["items"][0]["id"]
    assert (await client.get(f"/api/v1/sessions/{sid}/health", headers=op)).json()["health"] == "Healthy"
    assert len((await client.get(f"/api/v1/sessions/{sid}/checks", headers=op)).json()) == 1
    r = await client.post("/api/v1/sessions/check", json={"session_ids": [sid]}, headers=op)
    assert r.json() == {"mode": "queued", "jobs": 1}
    assert (await client.get("/api/v1/jobs/stats", headers=op)).json()["PENDING"] == 1
    m = (await client.get("/api/v1/monitoring/metrics", headers=op)).json()
    assert m["database"]["ok"] and "cpu_percent" in m["system"] and m["queue"]["PENDING"] == 1
    a = (await client.get("/api/v1/monitoring/analytics", headers=op)).json()
    assert a["session_availability"]["total"] == 3
    assert (await client.get("/api/v1/backups/integrity", headers=op)).status_code == 403
    admin = await login(client, "admin")
    integ = (await client.get("/api/v1/backups/integrity", headers=admin)).json()
    assert integ["database_ok"] and integ["sessions_registered"] == 3


async def test_web_dashboard_pages(client, make_session_file):
    r = await client.get("/")
    assert r.status_code == 303 and "/login" in r.headers["location"]
    r = await client.post("/login", data={"username": "admin", "password": "admin-pass-123", "next": "/"})
    assert r.status_code == 303 and "tg_access" in r.headers.get("set-cookie", "")
    for path in ("/", "/sessions", "/accounts", "/proxies", "/targets", "/cases", "/submissions", "/errors", "/audit",
                 "/reports", "/settings"):
        r = await client.get(path)
        assert r.status_code == 200, path
    r = await client.post("/targets", data={"target_type": "channel", "url": "https://t.me/evilchan", "title": "Evil"})
    assert r.status_code == 303
    tid = (await client.get("/api/v1/targets")).json()["items"][0]["id"]
    r = await client.post("/cases", data={"target_id": tid, "title": "Web case", "reason_code": "illegal_content"})
    assert r.status_code == 303 and "/cases/" in r.headers["location"]
    detail = await client.get(r.headers["location"])
    assert detail.status_code == 200 and "Web case" in detail.text and "Generate draft" in detail.text
    r = await client.get("/sessions", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "<table" in r.text and "<html" not in r.text
