@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
"%PY%" scripts\compose_all.py
echo.
pause
