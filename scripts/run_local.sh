#!/usr/bin/env bash
# One-step local launcher for macOS / Linux. All logic lives in scripts/setup_and_run.py.
#   ./scripts/run_local.sh                 # local SQLite database in data/tglegal.db
#   ./scripts/run_local.sh --port 8080     # extra args pass through to the launcher
# Requires Python 3.11+. The server listens on 127.0.0.1 only. Stop it with Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")/.."
PYBIN="${PYTHON:-python3}"
command -v "$PYBIN" >/dev/null || { echo "[X] Python 3.11+ not found. Install it, then re-run."; exit 1; }
exec "$PYBIN" scripts/setup_and_run.py "$@"
