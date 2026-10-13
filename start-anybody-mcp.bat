@echo off
setlocal
rem Resolve the repository from this script's own location, so the launcher keeps
rem working if the checkout is moved or cloned somewhere else.
set "REPO=%~dp0"

if not exist "%REPO%.venv\Scripts\python.exe" (
  echo Python venv not found at "%REPO%.venv" 1>&2
  echo Create it with:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt 1>&2
  exit /b 1
)

rem stdout carries JSON-RPC frames only; diagnostics go to stderr.
"%REPO%.venv\Scripts\python.exe" "%REPO%src\anybody_mcp_server.py"
