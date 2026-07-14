@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" http://localhost:8777/templates/preview.html
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m http.server 8777
) else (
  python -m http.server 8777
)
