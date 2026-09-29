#!/usr/bin/env bash
# One-step local launcher for macOS / Linux (mirror of scripts/run_windows.bat).
#   ./scripts/run_local.sh                 # local SQLite database in data/tglegal.db
#   ./scripts/run_local.sh --port 8080     # any extra args are passed to `serve`
# Requires Python 3.11+. The server listens on 127.0.0.1 only. Stop it with Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")/.."

PYBIN="${PYTHON:-python3}"
command -v "$PYBIN" >/dev/null || { echo "[X] Python 3.11+ not found. Install it, then re-run."; exit 1; }

if [ ! -x ".venv/bin/python" ]; then
  echo "[*] Creating virtual environment .venv ..."
  "$PYBIN" -m venv .venv
  echo "[*] Installing the platform ..."
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -e .
fi

[ -f .env ] || { echo "[*] Writing local .env ..."; .venv/bin/python scripts/make_env.py; }
mkdir -p data sessions/active sessions/disabled sessions/quarantined

echo "[*] Applying database migrations ..."
.venv/bin/python -m alembic upgrade head

echo "[*] Seeding the reason catalog and report templates ..."
.venv/bin/python -m app.cli.main db seed

echo "[*] Ensuring an admin user exists ..."
.venv/bin/python -m app.cli.main users ensure-admin

echo
echo "[OK] Starting the dashboard at http://127.0.0.1:8000  (Ctrl+C to stop)"
exec .venv/bin/python -m app.cli.main serve --host 127.0.0.1 --port 8000 "$@"
