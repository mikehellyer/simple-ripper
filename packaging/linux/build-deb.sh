#!/usr/bin/env bash
# Packages dist/Simple-Ripper/ (already built by PyInstaller, onedir mode)
# into a .deb that installs to /opt/simple-ripper and adds a proper menu
# entry + icon, so it behaves like a native app rather than a dropped
# binary. Uses dpkg-deb directly, which ships with Debian-based distros
# (Pop!_OS included) — no extra tool to install in CI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUNDLE="$ROOT/dist/Simple-Ripper"
VERSION="$(grep -o '__version__ = "[^"]*"' "$ROOT/src/simple_ripper/__init__.py" | cut -d'"' -f2)"
ARCH="$(dpkg --print-architecture)"
PKG_ROOT="$ROOT/dist/deb-staging"
OUT="$ROOT/dist/simple-ripper_${VERSION}_${ARCH}.deb"

if [ ! -d "$BUNDLE" ]; then
  echo "error: $BUNDLE not found — run PyInstaller first" >&2
  exit 1
fi

rm -rf "$PKG_ROOT" "$OUT"
mkdir -p "$PKG_ROOT/DEBIAN"
mkdir -p "$PKG_ROOT/opt/simple-ripper"
mkdir -p "$PKG_ROOT/usr/bin"
mkdir -p "$PKG_ROOT/usr/share/applications"

cp -R "$BUNDLE"/. "$PKG_ROOT/opt/simple-ripper/"
ln -s /opt/simple-ripper/Simple-Ripper "$PKG_ROOT/usr/bin/simple-ripper"
cp "$ROOT/packaging/linux/simple-ripper.desktop" "$PKG_ROOT/usr/share/applications/"

for size in 16 32 48 64 128 256 512; do
  dir="$PKG_ROOT/usr/share/icons/hicolor/${size}x${size}/apps"
  mkdir -p "$dir"
  cp "$ROOT/packaging/icons/icon_${size}.png" "$dir/simple-ripper.png"
done

cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: simple-ripper
Version: $VERSION
Section: sound
Priority: optional
Architecture: $ARCH
Maintainer: Mike Hellyer
Recommends: cdparanoia
Description: Simple CD ripper with MusicBrainz tagging
 Rips audio CDs to FLAC, MP3, Ogg, Opus, AAC, ALAC or WAV, looks the
 disc up on MusicBrainz and tags the files the way Picard does, with an
 in-app update checker.
EOF

dpkg-deb --build --root-owner-group "$PKG_ROOT" "$OUT"
rm -rf "$PKG_ROOT"

echo "wrote $OUT"
