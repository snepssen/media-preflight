@echo off
rem Starts the local server and opens the window, and offers to install anything
rem the tool needs first.
rem
rem Like the macOS bundle, this exists to fail in sentences: the tool needs a
rem Python and an ffmpeg it does not ship, and a script that dies silently when
rem one is missing leaves somebody staring at a window that never opens. Python
rem is still somebody else's job to install, because nothing here can run before
rem it exists; everything after that is the bootstrap's, and it asks rather than
rem instructs.
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

rem Exit 0 means everything required is present. Anything else, ask — the
rem offer lists what is missing, shows the winget line for each, and defaults
rem to installing all of it.
python bootstrap.py --check
if errorlevel 1 (
  python bootstrap.py
  if errorlevel 1 (
    echo.
    echo Media Preflight cannot start until the programs above are installed.
    echo Close and reopen this window afterwards so it picks up the new PATH.
    pause
    exit /b 1
  )
)

python app.py %*
