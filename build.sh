#!/usr/bin/env bash
# Build the things somebody can double-click.
#
#   ./build.sh            everything this platform can make
#   ./build.sh app        Media Preflight.app          (macOS only)
#   ./build.sh pyz        media-preflight.pyz          (anywhere)
#
# What this does NOT do is bundle a Python or an ffmpeg, and that is a decision
# rather than an omission. Bundling Python would mean taking a build-time
# dependency — PyInstaller or py2app — on a tool whose entire claim is that it
# needs nothing installed, and every person who then wanted to build it would
# need that dependency too. Bundling ffmpeg means shipping eighty megabytes and
# inheriting a licensing decision that belongs to whoever redistributes it.
#
# So the bundle removes the terminal, not the prerequisites: it finds a Python
# and an ffmpeg, and when it cannot, it says which one is missing and prints
# the command that installs it. That is the honest version of "double-click to
# run" for a program that is, underneath, a front end to ffmpeg.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[ -n "$VERSION" ] || { echo "VERSION is empty" >&2; exit 1; }
BUILD="$ROOT/build"

# Everything the application needs at runtime, and nothing else: no tests, no
# fixtures, no build scripts. A bundle that carries its own test suite is a
# bundle nobody checked the contents of.
MODULES=(analysis.py app.py batch.py captions.py chart.py checks.py \
         corrections.py index.html intake.py platform_support.py \
         preflight.py probe.py profiles.py report.py video.py __main__.py)

say() { printf '  %s\n' "$*"; }

build_pyz() {
  local staging="$BUILD/pyz"
  rm -rf "$staging"
  mkdir -p "$staging/tools"
  for module in "${MODULES[@]}"; do cp "$ROOT/$module" "$staging/"; done
  cp "$ROOT/tools/check_ffmpeg.py" "$staging/tools/"
  [ -d "$ROOT/profiles" ] && cp -R "$ROOT/profiles" "$staging/" || true

  python3 -m zipapp "$staging" \
    --output "$BUILD/media-preflight.pyz" \
    --python "/usr/bin/env python3" --compress
  chmod +x "$BUILD/media-preflight.pyz"
  rm -rf "$staging"

  # A build nobody ran is a build that does not work. Prove the archive
  # executes and answers before calling it finished.
  local listed
  listed="$("$BUILD/media-preflight.pyz" targets | head -1)"
  case "$listed" in
    acx*) say "media-preflight.pyz  ($(du -h "$BUILD/media-preflight.pyz" | cut -f1)) — runs, lists targets" ;;
    *) echo "the .pyz did not answer 'targets' as expected: $listed" >&2; exit 1 ;;
  esac
}

build_app() {
  [ "$(uname)" = "Darwin" ] || { say "skipping the .app: not macOS"; return; }
  local app="$BUILD/Media Preflight.app"
  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/tools"

  for module in "${MODULES[@]}"; do
    cp "$ROOT/$module" "$app/Contents/Resources/"
  done
  cp "$ROOT/tools/check_ffmpeg.py" "$app/Contents/Resources/tools/"
  [ -d "$ROOT/profiles" ] && cp -R "$ROOT/profiles" "$app/Contents/Resources/" || true
  cp "$ROOT/packaging/launcher.sh" "$app/Contents/MacOS/Media Preflight"
  chmod +x "$app/Contents/MacOS/Media Preflight"

  build_icon "$app/Contents/Resources/appicon.icns"

  cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Media Preflight</string>
  <key>CFBundleDisplayName</key><string>Media Preflight</string>
  <key>CFBundleIdentifier</key><string>com.snepssen.media-preflight</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>Media Preflight</string>
  <key>CFBundleIconFile</key><string>appicon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

  # An unsigned bundle is quarantined on first open. Ad-hoc signing does not
  # avoid that, but it does stop macOS reporting the app as damaged after any
  # later change to its contents.
  codesign --force --deep --sign - "$app" >/dev/null 2>&1 \
    || say "note: could not ad-hoc sign (codesign unavailable)"

  say "Media Preflight.app — drag it to /Applications"
}

build_icon() {
  local target="$1"
  local iconset="$BUILD/icon.iconset"
  rm -rf "$iconset"
  mkdir -p "$iconset"
  python3 "$ROOT/tools/icon.py" "$BUILD/icons" >/dev/null
  for size in 16 32 128 256 512; do
    cp "$BUILD/icons/icon_${size}.png" "$iconset/icon_${size}x${size}.png"
    cp "$BUILD/icons/icon_$((size * 2)).png" \
       "$iconset/icon_${size}x${size}@2x.png"
  done
  # macOS 27 beta's iconutil rejects the same conventional ten-file iconset
  # that earlier versions accept. Keep Apple's validator as the first choice,
  # then pack those PNG representations directly as an ICNS chunk container.
  if ! iconutil --convert icns "$iconset" --output "$target" 2>/dev/null; then
    say "note: iconutil rejected the iconset; using the ICNS fallback"
    python3 "$ROOT/tools/make_icns.py" "$iconset" "$target"
  fi
  [ -s "$target" ] || { echo "the application icon was not built" >&2; exit 1; }
  rm -rf "$iconset" "$BUILD/icons"
}

build_desktop() {
  cat > "$BUILD/media-preflight.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Media Preflight
Comment=Check finished media against a delivery target
Exec=$ROOT/start.sh
Icon=$BUILD/icon_256.png
Terminal=false
Categories=AudioVideo;Audio;Video;
DESKTOP
  python3 "$ROOT/tools/icon.py" "$BUILD/icons" >/dev/null
  mv "$BUILD/icons/icon_256.png" "$BUILD/icon_256.png"
  rm -rf "$BUILD/icons"
  say "media-preflight.desktop — copy it to ~/.local/share/applications/"
}

mkdir -p "$BUILD"
echo "Media Preflight $VERSION"
case "${1:-all}" in
  app) build_app ;;
  pyz) build_pyz ;;
  desktop) build_desktop ;;
  all) build_pyz; build_app; build_desktop ;;
  *) echo "usage: $0 [all|app|pyz|desktop]" >&2; exit 2 ;;
esac
echo "  in $BUILD"
