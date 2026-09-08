@echo off
rem Starts the local server and opens the window.
rem
rem Like the macOS bundle, this exists to fail in sentences: the tool needs a
rem Python and an ffmpeg it does not ship, and a script that dies silently when
rem one is missing leaves somebody staring at a window that never opens.
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Media Preflight needs Python 3.10 or newer, and could not find one.
  echo.
  echo   Install it from python.org, or run:  winget install Python.Python.3.12
  echo.
  pause
  exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo The Python on this machine is older than 3.10.
  echo.
  echo   Install a newer one:  winget install Python.Python.3.12
  echo.
  pause
  exit /b 1
)

python tools\check_ffmpeg.py
if errorlevel 1 (
  echo Media Preflight needs ffmpeg, built with the ebur128 filter,
  echo and could not find one.
  echo.
  echo   Install it:  winget install Gyan.FFmpeg
  echo.
  echo Close and reopen this window afterwards so it picks up the new PATH.
  pause
  exit /b 1
)

python app.py %*
