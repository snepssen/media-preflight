#!/bin/sh
# Double-click this on macOS. It starts the local server and opens the window.
#
# It hands over to start.sh rather than running app.py itself, so that a
# double-click and a terminal get the same offer to install what is missing
# instead of two launchers that drift apart on the question.
exec "$(dirname "$0")/start.sh"
