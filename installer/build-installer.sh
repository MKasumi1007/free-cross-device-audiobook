#!/bin/bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_VERSION="0.5.1"
OUTPUT="${1:-$ROOT/dist/installer}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/audiobook-package.XXXXXX")"
trap '/bin/rm -rf "$WORK"' EXIT
APP="$WORK/米兰读书安装器.app"

/usr/bin/ditto "$ROOT/installer/apps/米兰读书安装器.app" "$APP"
mkdir -p "$APP/Contents/Resources" "$OUTPUT"
/usr/bin/tar -czf "$APP/Contents/Resources/source.tar.gz" \
  --exclude='.git' --exclude='.venv' --exclude='.local' --exclude='node_modules' \
  --exclude='dist' --exclude='playwright-report' --exclude='test-results' \
  -C "$(dirname "$ROOT")" "$(basename "$ROOT")"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUTPUT/米兰读书安装器-$APP_VERSION.zip"
/usr/bin/shasum -a 256 "$OUTPUT/米兰读书安装器-$APP_VERSION.zip" > "$OUTPUT/米兰读书安装器-$APP_VERSION.zip.sha256"
echo "$OUTPUT/米兰读书安装器-$APP_VERSION.zip"
