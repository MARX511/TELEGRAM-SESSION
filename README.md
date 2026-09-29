# Telegram Session & Legal Reporting Platform

Session registry + health monitoring for an authorised operator's own Telegram sessions, and a case / evidence /
official-reporting workflow for legal & compliance teams. PostgreSQL, FastAPI, async SQLAlchemy, Web dashboard + CLI.

**Scope boundary.** Reports are filed once per case through Telegram's *official* channels (abuse@/dmca@/stopCA@ e-mail,
support portal, in-app report) by a human operator with explicit approval. The platform prepares, tracks and audits
those submissions. It does **not** send reports through Telegram user sessions, does not fan reports out across accounts,
and does not rotate proxies/sessions to evade limits. See `docs/SECURITY_MODEL.md`.

## Quick start (local)
```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env                       # set DATABASE_URL (PostgreSQL) and secrets
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli.main users create admin --role admin
.venv/bin/python -m app.cli.main sessions scan && .venv/bin/python -m app.cli.main sessions check --all
.venv/bin/python -m app.cli.main serve      # http://localhost:8000  (API docs: /api/docs)
```
Dashboard assets are self-hosted (no CDN); rebuild them after template changes with `scripts/build_assets.sh`.
Docker: `docker compose up` (PostgreSQL + Redis + app). Tests: `.venv/bin/pytest`. Benchmark: `python -m benchmarks --db <scratch-db-url>`.

## Documents
`docs/PROJECT_STATE.md` · `ARCHITECTURE.md` · `DATABASE_DESIGN.md` · `SESSION_ARCHITECTURE.md` · `CASE_WORKFLOW.md` ·
`API_CONTRACT.md` · `TEST_PLAN.md` · `SECURITY_MODEL.md` · `IMPLEMENTATION_PLAN.md`
