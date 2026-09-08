#!/bin/sh
# Starts the local server and opens the window.
#
# It checks the two things the tool needs and does not ship, so that a missing
# ffmpeg is a sentence rather than a window that never appears.
cd "$(dirname "$0")" || exit 1

for candidate in python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "${PYTHON:-}" ]; then
  echo "Media Preflight needs Python 3.10 or newer, and could not find one." >&2
  exit 1
fi

if ! "$PYTHON" tools/check_ffmpeg.py; then
  exit 1
fi

exec "$PYTHON" app.py "$@"
