@echo off
REM ==========================================================================
REM  Local launcher for Windows. Double-click this file, or run it from a
REM  Command Prompt opened in the project folder.
REM
REM  What it does (all standard, documented steps):
REM    1. creates a .venv virtual environment (first run only)
REM    2. installs the project into it (first run only)
REM    3. writes a local .env with a SQLite database (first run only)
REM    4. applies database migrations
REM    5. creates the first admin user if none exists (asks for a password)
REM    6. starts the dashboard on http://127.0.0.1:8000  (this PC only)
REM
REM  Requirements: Python 3.11+ on PATH (https://www.python.org/downloads/).
REM  Stop the server with Ctrl+C in this window.
REM ==========================================================================
setlocal
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python was not found on PATH. Install Python 3.11+ from python.org,
  echo     tick "Add python.exe to PATH" during setup, then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [*] Creating virtual environment .venv ...
  python -m venv .venv || (echo [X] venv creation failed & pause & exit /b 1)
  echo [*] Installing the platform (this can take a couple of minutes) ...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -e . || (echo [X] install failed & pause & exit /b 1)
)

if not exist ".env" (
  echo [*] Writing local .env (SQLite database in data\tglegal.db) ...
  ".venv\Scripts\python.exe" scripts\make_env.py || (echo [X] could not write .env & pause & exit /b 1)
)

if not exist "data" mkdir data
if not exist "sessions\active" mkdir sessions\active
if not exist "sessions\disabled" mkdir sessions\disabled
if not exist "sessions\quarantined" mkdir sessions\quarantined

echo [*] Applying database migrations ...
".venv\Scripts\python.exe" -m alembic upgrade head || (echo [X] migration failed & pause & exit /b 1)

echo [*] Seeding the reason catalog and report templates ...
".venv\Scripts\python.exe" -m app.cli.main db seed

echo [*] Ensuring an admin user exists ...
".venv\Scripts\python.exe" -m app.cli.main users ensure-admin

echo.
echo [OK] Starting the dashboard at http://127.0.0.1:8000
echo      Open that address in your browser. Press Ctrl+C here to stop.
echo.
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m app.cli.main serve --host 127.0.0.1 --port 8000

endlocal
