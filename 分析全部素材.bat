@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\analyze_all.py
) else (
  python scripts\analyze_all.py
)
pause
