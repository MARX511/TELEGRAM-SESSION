# Telegram Session & Legal Reporting Platform

Session registry + health monitoring for an authorised operator's own Telegram sessions, and a case / evidence /
official-reporting workflow for legal & compliance teams. PostgreSQL, FastAPI, async SQLAlchemy, Web dashboard + CLI.

**Scope boundary.** Reports are filed once per case through Telegram's *official* channels (abuse@/dmca@/stopCA@ e-mail,
support portal, in-app report) by a human operator with explicit approval. The platform prepares, tracks and audits
those submissions. It does **not** send reports through Telegram user sessions, does not fan reports out across accounts,
and does not rotate proxies/sessions to evade limits. See `docs/SECURITY_MODEL.md`.

## Run it on your own computer (one step)

Install Python 3.11+ first (on Windows tick "Add python.exe to PATH"). Then:

- **Windows:** double-click `scripts\run_windows.bat` (or run it from a Command Prompt in the project folder).
- **macOS / Linux:** `./scripts/run_local.sh`

The launcher creates the virtual environment, installs the platform, writes a local `.env` with a **SQLite**
database (no database server needed) and fresh secrets, migrates the database, asks once for an admin password,
then starts the dashboard at **http://127.0.0.1:8000** (this computer only) and opens it in your browser. Stop it
with Ctrl+C. Re-running the launcher just starts the server again.

To use **PostgreSQL** instead of SQLite, generate the `.env` with a database URL before the first launch:
`python scripts/make_env.py --database-url postgresql+asyncpg://user:pass@localhost:5432/tglegal`.

## Quick start (manual)
```bash
uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]"
python scripts/make_env.py                 # writes .env (SQLite + fresh secrets); or copy .env.example
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli.main db seed   # reason catalog + report templates
.venv/bin/python -m app.cli.main users ensure-admin
.venv/bin/python -m app.cli.main sessions scan && .venv/bin/python -m app.cli.main sessions check --all
.venv/bin/python -m app.cli.main serve     # http://localhost:8000  (API docs: /api/docs)
```
Dashboard assets are self-hosted (no CDN); rebuild them after template changes with `scripts/build_assets.sh`.
Docker: `docker compose up` (PostgreSQL + Redis + app). Tests: `.venv/bin/pytest`. Benchmark: `python -m benchmarks --db <scratch-db-url>`.

## Documents
`docs/PROJECT_STATE.md` · `ARCHITECTURE.md` · `DATABASE_DESIGN.md` · `SESSION_ARCHITECTURE.md` · `CASE_WORKFLOW.md` ·
`API_CONTRACT.md` · `TEST_PLAN.md` · `SECURITY_MODEL.md` · `IMPLEMENTATION_PLAN.md`
