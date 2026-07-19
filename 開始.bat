@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM First run: create venv + install deps, then launch the editor.
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo First run - installing...
  call "%~dp0_py.bat"
  "%PY%" scripts\setup.py
)
call "%~dp0_py.bat"
"%PY%" scripts\editor_server.py
pause
