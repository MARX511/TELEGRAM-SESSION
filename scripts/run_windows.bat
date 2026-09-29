@echo off
REM Trivial wrapper: all setup logic lives in scripts\setup_and_run.py, so this
REM file has no multi-line blocks and works whatever the line endings are.
REM Double-click it, or run it from a Command Prompt in the project folder.
cd /d "%~dp0.."
set "PY=python"
where py >nul 2>nul && set "PY=py"
where %PY% >nul 2>nul || goto nopy
%PY% scripts\setup_and_run.py
goto done
:nopy
echo.
echo [X] Python was not found on this computer.
echo     Install Python 3.11 or newer from https://www.python.org/downloads/
echo     During setup, TICK the box "Add python.exe to PATH", then run this again.
:done
echo.
pause
