@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" sync_data.py --monthly
) else (
  python sync_data.py --monthly
)
exit /b %errorlevel%
