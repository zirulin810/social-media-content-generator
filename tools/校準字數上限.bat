@echo off
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0..\_py.bat"
"%PY%" scripts\calibrate.py
echo.
pause
