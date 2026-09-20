#!/bin/sh
# Starts the local server and opens the window, and offers to install anything
# the tool needs first.
#
# The check is separate from the offer on purpose: somebody who already has
# everything should see the window, not a question. Only a missing program turns
# this into a conversation, and the default answer to that is yes.
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
  echo "On macOS:  brew install python" >&2
  exit 1
fi

# Exit 0 means everything required is present. Anything else, ask.
if ! "$PYTHON" bootstrap.py --check; then
  "$PYTHON" bootstrap.py || exit 1
fi

exec "$PYTHON" app.py "$@"
