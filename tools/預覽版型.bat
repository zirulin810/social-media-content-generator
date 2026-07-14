@echo off
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0..\_py.bat"
start "" http://localhost:8777/templates/preview.html
"%PY%" -m http.server 8777
