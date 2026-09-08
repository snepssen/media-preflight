#!/usr/bin/env bash
# Package what build.sh makes into things somebody can download.
#
#   ./release.sh            test, build, package and verify into dist/
#   ./release.sh --skip-tests   package what is already here
#
# This stops at the edge of publishing. It writes dist/ and prints the command
# that would create the GitHub release; it does not run it. Putting a binary
# in front of the public is a decision, and a script that makes it silently is
# a script that will one day make it by accident.
#
# What is in a release, and why there are three of them:
#
#   media-preflight-<v>.pyz          one file, every platform, needs python3
#   Media-Preflight-<v>-macOS.zip    the .app, for people who want an icon
#   media-preflight-<v>-linux.tar.gz the .pyz, a launcher and a .desktop entry
#   media-preflight-<v>-windows.zip  the .pyz and a .bat launcher
#
# None of them bundle Python or ffmpeg — see the note at the top of build.sh
# for why that is a decision rather than an omission. Every archive therefore
# carries INSTALL.txt saying what it needs and how to get it, because a
# download that fails silently on a machine missing ffmpeg is worse than no
# download at all.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[ -n "$VERSION" ] || { echo "VERSION is empty" >&2; exit 1; }
BUILD="$ROOT/build"
DIST="$ROOT/dist"
SKIP_TESTS=0
[ "${1:-}" = "--skip-tests" ] && SKIP_TESTS=1

say() { printf '  %s\n' "$*"; }
step() { printf '\n%s\n' "$*"; }

# --------------------------------------------------------------- the tests
#
# A release that cannot pass its own suite is not a release. This is the one
# check that costs half a minute and prevents shipping something that was
# broken before it was ever packaged.
if [ "$SKIP_TESTS" -eq 0 ]; then
  step "Tests"
  if python3 -m unittest discover -s tests -q >/dev/null 2>&1; then
    say "the suite passes"
  else
    echo "The test suite fails. Not packaging a broken build." >&2
    python3 -m unittest discover -s tests -q 2>&1 | tail -20 >&2
    exit 1
  fi
fi

step "Build"
bash "$ROOT/build.sh" >/dev/null
[ -f "$BUILD/media-preflight.pyz" ] || { echo "no .pyz was built" >&2; exit 1; }
say "$(du -h "$BUILD/media-preflight.pyz" | cut -f1) archive"

rm -rf "$DIST"
mkdir -p "$DIST"

# ------------------------------------------------------------ what it needs
install_note() {
  cat <<NOTE
Media Preflight $VERSION

This is an inspector for finished audio, video and caption files. It reads
your media, tells you whether a delivery target would accept it, and can
write a corrected copy. It never modifies the file you give it.

It needs two things it does not ship:

  Python 3.10 or newer
  ffmpeg, built with the ebur128 filter

Neither is bundled, deliberately. Bundling Python would mean everyone who
builds this needs a packaging toolchain; bundling ffmpeg means eighty
megabytes and a redistribution licensing decision that is not ours to make.
So this fails in sentences instead: if either is missing, it says which one
and prints the command that installs it.

  macOS      brew install ffmpeg
  Windows    winget install Gyan.FFmpeg
  Debian     sudo apt install ffmpeg
  Fedora     sudo dnf install ffmpeg

Running it
----------
$1

Nothing is uploaded, no account is needed, and no part of this calls out to a
network. MIT licensed.
NOTE
}

# ---------------------------------------------------------------- the .pyz
step "One file, every platform"
cp "$BUILD/media-preflight.pyz" "$DIST/media-preflight-$VERSION.pyz"
say "media-preflight-$VERSION.pyz"

# --------------------------------------------------------------- the macOS
step "macOS"
if [ -d "$BUILD/Media Preflight.app" ]; then
  # ditto rather than zip: a .app is a bundle with a signature and symlinks in
  # it, and zip -r flattens exactly the things that make it launchable.
  ( cd "$BUILD" && ditto -c -k --keepParent --sequesterRsrc \
      "Media Preflight.app" "$DIST/Media-Preflight-$VERSION-macOS.zip" )
  say "Media-Preflight-$VERSION-macOS.zip"
else
  say "skipped: no .app here (build it on macOS)"
fi

# --------------------------------------------------------------- the Linux
step "Linux"
LIN="$BUILD/pkg-linux/media-preflight-$VERSION"
rm -rf "$BUILD/pkg-linux"; mkdir -p "$LIN"
cp "$BUILD/media-preflight.pyz" "$LIN/"
cp "$ROOT/LICENSE" "$LIN/"
cat > "$LIN/media-preflight" <<'LAUNCH'
#!/bin/sh
# Opens the window. Any argument that names a command runs the command line
# instead: media-preflight check film.mov --target youtube
cd "$(dirname "$0")" || exit 1
for candidate in python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    exec "$candidate" media-preflight.pyz "$@"
  fi
done
echo "Media Preflight needs Python 3.10 or newer, and could not find one." >&2
echo "  Debian/Ubuntu   sudo apt install python3" >&2
echo "  Fedora          sudo dnf install python3" >&2
exit 1
LAUNCH
chmod +x "$LIN/media-preflight"
cat > "$LIN/media-preflight.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Media Preflight
Comment=Check finished media against a delivery target
Exec=$HOME/.local/opt/media-preflight/media-preflight
Terminal=false
Categories=AudioVideo;Audio;Video;
DESKTOP
install_note "  ./media-preflight

To get a menu entry, put this folder at ~/.local/opt/media-preflight and copy
media-preflight.desktop into ~/.local/share/applications/." > "$LIN/INSTALL.txt"
( cd "$BUILD/pkg-linux" && tar -czf "$DIST/media-preflight-$VERSION-linux.tar.gz" \
    "media-preflight-$VERSION" )
say "media-preflight-$VERSION-linux.tar.gz"

# ------------------------------------------------------------- the Windows
step "Windows"
WIN="$BUILD/pkg-windows/media-preflight-$VERSION"
rm -rf "$BUILD/pkg-windows"; mkdir -p "$WIN"
cp "$BUILD/media-preflight.pyz" "$WIN/"
cp "$ROOT/LICENSE" "$WIN/"
# CRLF, because this is opened in Notepad on a machine that has never heard of
# a bare line feed.
printf '@echo off\r\nrem Opens the window. An argument naming a command runs the command line:\r\nrem   media-preflight.bat check film.mov --target youtube\r\nsetlocal\r\ncd /d "%%~dp0"\r\n\r\nwhere python >nul 2>&1\r\nif errorlevel 1 (\r\n  echo Media Preflight needs Python 3.10 or newer, and could not find one.\r\n  echo   Install it:  winget install Python.Python.3.12\r\n  pause\r\n  exit /b 1\r\n)\r\n\r\npython media-preflight.pyz %%*\r\n' > "$WIN/media-preflight.bat"
install_note "  Double-click media-preflight.bat" | sed 's/$/\r/' > "$WIN/INSTALL.txt"
( cd "$BUILD/pkg-windows" && zip -qr \
    "$DIST/media-preflight-$VERSION-windows.zip" "media-preflight-$VERSION" )
say "media-preflight-$VERSION-windows.zip"

# ---------------------------------------------------------------- verifying
#
# Every archive is opened somewhere else and run, because "it built" and "it
# works when unpacked" are different claims and only the second one is what
# somebody downloading it finds out.
step "Verify"
CHECK="$BUILD/verify"
rm -rf "$CHECK"; mkdir -p "$CHECK"

( cd "$CHECK" && tar -xzf "$DIST/media-preflight-$VERSION-linux.tar.gz" )
out="$( "$CHECK/media-preflight-$VERSION/media-preflight" targets | head -1 )"
case "$out" in
  acx*) say "linux tarball unpacks and runs" ;;
  *) echo "the linux tarball did not answer 'targets': $out" >&2; exit 1 ;;
esac

( cd "$CHECK" && unzip -qo "$DIST/media-preflight-$VERSION-windows.zip" )
out="$( python3 "$CHECK/media-preflight-$VERSION/media-preflight.pyz" targets | head -1 )"
case "$out" in
  acx*) say "windows archive unpacks and runs" ;;
  *) echo "the windows archive did not answer 'targets': $out" >&2; exit 1 ;;
esac

if [ -f "$DIST/Media-Preflight-$VERSION-macOS.zip" ]; then
  ( cd "$CHECK" && ditto -x -k "$DIST/Media-Preflight-$VERSION-macOS.zip" mac )
  app="$CHECK/mac/Media Preflight.app"
  [ -x "$app/Contents/MacOS/Media Preflight" ] \
    || { echo "the .app lost its executable bit in the zip" >&2; exit 1; }
  codesign --verify --deep "$app" 2>/dev/null \
    && say "macOS bundle survives the round trip, signature intact" \
    || say "macOS bundle unpacks; signature is ad-hoc and does not verify"
fi

# --------------------------------------------------------------- checksums
step "Checksums"
( cd "$DIST" && if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 ./* > SHA256SUMS
  else
    sha256sum ./* > SHA256SUMS
  fi )
sed 's/^/  /' "$DIST/SHA256SUMS"

step "Ready in dist/"
say "Nothing has been published. To make the release:"
printf '\n    gh release create v%s dist/* \\\n      --title "Media Preflight %s" \\\n      --notes-file RELEASE_NOTES.md\n\n' "$VERSION" "$VERSION"
say "macOS note worth putting in the notes: the .app is ad-hoc signed, not"
say "notarised, so a downloaded copy is quarantined. Either right-click and"
say "choose Open, or run:  xattr -dr com.apple.quarantine '/Applications/Media Preflight.app'"
