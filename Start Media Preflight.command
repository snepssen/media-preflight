#!/bin/sh
# Double-click this on macOS. It starts the local server and opens the window.
cd "$(dirname "$0")" || exit 1
exec python3 app.py
