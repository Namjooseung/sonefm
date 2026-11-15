@echo off
rem SoneFM platform (dev, demo data): run from repo without building exe
rem Uses demo-data\ instead of Documents\SoneFM. Delete demo-data to start over.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo .venv not found. Run: py -3.12 -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements-dev.txt
  pause
  exit /b 1
)
set SONEFM_HOME=%~dp0demo-data
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe tools\demo_setup.py
start "" ".venv\Scripts\pythonw.exe" -m hub
