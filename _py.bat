@echo off
REM Single source of truth for "which python".
REM
REM Bug this exists to prevent (2026-07-14): the install script called bare
REM `python` while the run scripts used .venv\Scripts\python.exe. Packages went
REM into the system Python; the code ran inside the venv and could not find them.
REM "I did run the installer" -- yes, into the wrong interpreter.
REM
REM Every .bat must go through here. tests/test_windows_encoding.py enforces it.
if exist "%~dp0.venv\Scripts\python.exe" (
  set "PY=%~dp0.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
