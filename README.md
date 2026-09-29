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
Docker: `docker compose up` (PostgreSQL + Redis + app). Tests: `.venv/bin/pytest`. Benchmark: `python -m benchmarks --db <scratch-db-url>`.

## Session files
- **Upload** from the Sessions page (drag `.session` files or a `.zip` of them onto the upload box) or
  `POST /api/v1/sessions/upload`. Each file must be a Telethon or Pyrogram session database; it is validated,
  de-duplicated by content, saved to `sessions/active` under a safe name (encrypted at rest when
  `SESSION_FILE_ENCRYPTION_KEY` is set) and registered. ZIP members are extracted by basename only, with size caps.
- Or copy files into `sessions/active` yourself and press **Scan folder**.
- **Check mode:** `TELEGRAM_PROVIDER=simulation` (default) produces simulated results. For real checks set
  `TELEGRAM_PROVIDER=telethon`, `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` (from my.telegram.org) in `.env` and run the
  launcher again: it installs `telethon` automatically. The Sessions page shows which mode is active and what is missing.

## Official reporting channels
The platform ships Telegram's documented official channels as a ready-made catalog, so nothing is typed by hand:
- **Emails:** `abuse@` (illegal content, terrorism, fraud, impersonation, doxxing), `dmca@` (copyright),
  `stopCA@` (child safety), `sticker-abuse@telegram.org`.
- **Portals / handles:** the in-app Report button, `@ISISwatch` (terrorism), `@notoscam` (scams),
  `telegram.org/support`, and the EU DSA form `telegram.org/dsa`.

When a case reaches **Ready**, the recipient is pre-selected from the case's reason (a copyright case defaults to
`dmca@`, spam to the in-app report, and so on); the picker lists the rest, grouped into emails and portals. Add
channels for your own jurisdiction without editing code by setting `OFFICIAL_CHANNELS_EXTRA` in `.env`, e.g.
`OFFICIAL_CHANNELS_EXTRA=[{"key":"legal@example.gov","kind":"email","label":"National regulator"}]`.

With `SUBMISSION_EMAIL_ENABLED=true` and `SMTP_HOST`, `SMTP_PORT` (587 STARTTLS or 465 TLS), `SMTP_USER`,
`SMTP_PASSWORD`, `SMTP_FROM`, an approved case is sent once to the chosen allow-listed address, with the case's file
evidence attached after an integrity check (15 MB total). Without SMTP an `.eml` file with the same content is
written to `exports/` for you to send from your own mailbox. Replies arrive in that mailbox; record them on the case
(**Record response**) to move it to Completed or Failed.

## Official case dossier
For reporting to a government or law-enforcement authority, the case page has **Official dossier** (also
`POST /api/v1/exports/cases/{id}/dossier`), which downloads one self-contained, tamper-evident ZIP:
`01_case_report.pdf`, `02_case.json`, `03_submissions.json`, `04_audit_trail.json`, the evidence under
`evidence/` (files + `manifest.json` with each file's SHA-256 and chain of custody), and a top-level
`MANIFEST.json` that hashes every file so the recipient can confirm nothing was altered. Requires `reports:export`.

**Not supported by design:** the platform never reports *from* the accounts in your `.session` files, and never sends
the same report from many accounts. Session files are a registry only; reporting is one report per case through an
official channel, filed by a human. See `docs/SECURITY_MODEL.md`.

## Dashboard
- **Arabic (RTL) by default, English (LTR)** with the language button in the top bar; the choice is kept in a `lang`
  cookie. UI strings live in `app/web/i18n.py` (English keys → Arabic); a test fails if a template string has no
  Arabic translation.
- **Dark and light themes** with the theme button (`theme` cookie, rendered server-side so there is no flash).
- **3D:** a real-time three.js scene on the sign-in screen (pauses in background tabs, still frame under
  "reduce motion", CSS fallback without WebGL), and light CSS 3D in the app (tilt cards, the dashboard cube).
- Charts are server-rendered (`app/web/charts.py`); every colour-coded chart has a legend or direct labels.
- Brand name and copyright owner are settings (`BRAND_NAME_AR/EN`, `COPYRIGHT_OWNER_AR/EN`); the copyright notice
  appears on the sign-in screen and in Settings → About.
- Everything is self-hosted (no CDN): fonts (IBM Plex, OFL), three.js (MIT), Lucide icons (ISC), htmx (BSD-2),
  each licence ships next to its files under `app/web/static`. After changing templates, CSS or the icon list
  run `scripts/build_assets.sh` (needs Node.js) to rebuild `app.css`, fonts, three.js and `partials/icons.html`.

## Documents
`docs/PROJECT_STATE.md` · `ARCHITECTURE.md` · `DATABASE_DESIGN.md` · `SESSION_ARCHITECTURE.md` · `CASE_WORKFLOW.md` ·
`API_CONTRACT.md` · `TEST_PLAN.md` · `SECURITY_MODEL.md` · `IMPLEMENTATION_PLAN.md`
