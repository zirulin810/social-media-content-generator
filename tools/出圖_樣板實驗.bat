@echo off
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0..\_py.bat"
"%PY%" scripts\render_sample.py --both
explorer "out\_sample"
pause
