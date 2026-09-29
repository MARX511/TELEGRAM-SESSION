"""Cross-platform local launcher. Run it with your system Python:

    py scripts\\setup_and_run.py            (Windows)
    python3 scripts/setup_and_run.py        (macOS / Linux)

It performs the documented setup steps (create a virtualenv, install the project, write a local .env with a
SQLite database, migrate, seed reasons/templates, create the first admin) and then starts the dashboard on
http://127.0.0.1:8000 and opens it in the browser. All logic lives here, so the .bat wrapper stays trivial and
is not sensitive to line endings. Standard library only, so it runs before the virtualenv exists.

Flags:
    --no-serve        do everything except start the server (used for testing / CI)
    --no-browser      start the server but do not open a browser
    --port N          serve on port N (default 8000)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def step(msg: str) -> None:
    print(f"\n[*] {msg}", flush=True)


def fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"\n[X] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd: list[str], *, check: bool = True) -> int:
    print("    > " + " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if check and proc.returncode != 0:
        fail(f"command failed (exit {proc.returncode}): {' '.join(cmd)}")
    return proc.returncode


def ensure_venv() -> None:
    if VENV_PY.exists():
        return
    step("Creating the virtual environment (.venv)")
    run([sys.executable, "-m", "venv", str(VENV)])
    step("Installing the platform. This can take a few minutes on the first run")
    run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip"], check=False)
    run([str(VENV_PY), "-m", "pip", "install", "-e", "."])


def ensure_env() -> None:
    if (ROOT / ".env").exists():
        return
    step("Writing local settings (.env) with a SQLite database and fresh secrets")
    run([str(VENV_PY), str(ROOT / "scripts" / "make_env.py")])


def ensure_dirs() -> None:
    for sub in ("data", "sessions/active", "sessions/disabled", "sessions/quarantined"):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)


def prepare_database() -> None:
    step("Preparing the database (migrations, reasons, templates, admin)")
    run([str(VENV_PY), "-m", "alembic", "upgrade", "head"])
    run([str(VENV_PY), "-m", "app.cli.main", "db", "seed"], check=False)
    run([str(VENV_PY), "-m", "app.cli.main", "users", "ensure-admin"])


def open_browser_when_ready(url: str, timeout: float = 30.0) -> None:
    def _wait() -> None:
        deadline = time.time() + timeout
        health = url.rstrip("/") + "/healthz"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(health, timeout=1) as r:  # noqa: S310 (localhost only)
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_wait, daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser(description="Set up and run the platform locally.")
    ap.add_argument("--no-serve", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if sys.version_info < (3, 11):
        fail(f"Python 3.11 or newer is required (found {sys.version.split()[0]}). Install it from python.org.")

    print("=" * 60)
    print("   TG Legal Platform - local setup and launch")
    print("=" * 60)
    print(f"Project folder: {ROOT}")

    ensure_venv()
    ensure_env()
    ensure_dirs()
    prepare_database()

    if args.no_serve:
        step("Setup complete (--no-serve). Not starting the server.")
        return

    url = f"http://127.0.0.1:{args.port}"
    print("\n" + "=" * 60)
    print(f"   Dashboard:  {url}")
    print("   Sign in as 'admin' with the password you just set.")
    print("   KEEP THIS WINDOW OPEN. Press Ctrl+C here to stop.")
    print("=" * 60, flush=True)
    if not args.no_browser:
        open_browser_when_ready(url)
    try:
        run([str(VENV_PY), "-m", "app.cli.main", "serve", "--host", "127.0.0.1", "--port", str(args.port)])
    except KeyboardInterrupt:
        print("\n[*] Server stopped.")


if __name__ == "__main__":
    main()
