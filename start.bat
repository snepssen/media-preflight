@echo off
rem Starts the local server and opens the window.
cd /d "%~dp0"
python app.py %*
