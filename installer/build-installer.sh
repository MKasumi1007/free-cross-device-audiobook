#!/bin/bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_VERSION="0.5.2"
OUTPUT="${1:-$ROOT/dist/installer}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/audiobook-package.XXXXXX")"
trap '/bin/rm -rf "$WORK"' EXIT
APP="$WORK/米兰读书安装器.app"
ARCHIVE="$OUTPUT/MilanReader-Installer-$APP_VERSION.zip"

/usr/bin/ditto "$ROOT/installer/apps/米兰读书安装器.app" "$APP"
mkdir -p "$APP/Contents/Resources" "$OUTPUT"
/usr/bin/tar -czf "$APP/Contents/Resources/source.tar.gz" \
  --exclude='.git' --exclude='.venv' --exclude='.local' --exclude='node_modules' \
  --exclude='dist' --exclude='playwright-report' --exclude='test-results' \
  -C "$(dirname "$ROOT")" "$(basename "$ROOT")"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$ARCHIVE"
/usr/bin/shasum -a 256 "$ARCHIVE" > "$ARCHIVE.sha256"
echo "$ARCHIVE"
