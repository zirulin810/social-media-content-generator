@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
"%PY%" -m src.cli %*
echo.
pause
