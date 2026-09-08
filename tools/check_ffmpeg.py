#!/usr/bin/env python3
"""Exit 0 when a usable ffmpeg is present, 1 when it is not.

Split out so the .app launcher — a shell script that must not import the
application — can ask the same question the application will ask, and answer it
before the window fails to appear.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir))

import platform_support  # noqa: E402

if __name__ == "__main__":
    try:
        platform_support.require_tools()
    except platform_support.ToolsMissing:
        sys.exit(1)
    sys.exit(0)
