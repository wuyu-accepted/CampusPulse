@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 scripts\campus_pulse\run_full_system.py %*
) else (
  python scripts\campus_pulse\run_full_system.py %*
)

if errorlevel 1 (
  echo.
  echo CampusPulse full system failed to start. See README.md for prerequisites.
  pause
)
