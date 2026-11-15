@echo off
rem SoneFM schedule app (dev): run from repo without building exe
cd /d "%~dp0"
if not exist frontend\dist\index.html (
  echo frontend\dist\index.html not found. Run: cd frontend ^&^& npm run build
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" app\main.py
