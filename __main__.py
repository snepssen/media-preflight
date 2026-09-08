"""Entry point for the zipapp build.

`python3 media-preflight.pyz` opens the window; `python3 media-preflight.pyz
check file.wav --target acx` runs the command line. One archive, either way,
which is the difference between a tool somebody keeps and a folder they have
to remember the path to.
"""

import sys


def main():
    # A first argument that names a command belongs to the command line;
    # anything else — including nothing at all — opens the window.
    import preflight
    commands = {"check", "batch", "fix", "targets"}
    if len(sys.argv) > 1 and sys.argv[1] in commands:
        return preflight.main()
    import app
    app.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
