#!/bin/sh
# The executable inside Media Preflight.app.
#
# Its whole job is to fail in sentences. This tool needs two things it does not
# ship — a Python and an ffmpeg — and a bundle that dies silently when one is
# missing is worse than no bundle at all: the window simply never appears and
# there is nowhere to look. So each prerequisite is checked here, and its
# absence becomes a dialog with the command that fixes it.

BUNDLE="$(cd "$(dirname "$0")/../Resources" && pwd)"

say() {
  osascript -e "display dialog \"$1\" with title \"Media Preflight\" \
    buttons {\"OK\"} default button 1 with icon caution" >/dev/null 2>&1
}

# A Python new enough for the tool, wherever this Mac keeps it.
PYTHON=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
                 /usr/bin/python3 python3; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  say "Media Preflight needs Python 3.10 or newer, and could not find one.\n\nInstall it from python.org, or with Homebrew:\n\n    brew install python"
  exit 1
fi

if ! "$PYTHON" "$BUNDLE/tools/check_ffmpeg.py" 2>/dev/null; then
  say "Media Preflight needs ffmpeg, built with the ebur128 filter, and could not find one.\n\nInstall it with Homebrew:\n\n    brew install ffmpeg\n\nThe app will find it once it is installed."
  exit 1
fi

# Unbuffered, so that anybody who runs the bundle from a terminal to work out
# why it did not open sees the address and the errors as they happen rather
# than when the process finally exits.
exec "$PYTHON" -u "$BUNDLE/app.py" "$@"
