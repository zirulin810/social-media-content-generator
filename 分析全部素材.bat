@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
"%PY%" scripts\analyze_all.py
echo.
pause
