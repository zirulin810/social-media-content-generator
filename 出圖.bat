@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\render_sample.py --both
) else (
  python scripts\render_sample.py --both
)
explorer "out\_sample"
pause
