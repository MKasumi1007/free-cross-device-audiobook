#!/bin/bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${1:-$ROOT/dist/installer}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/audiobook-package.XXXXXX")"
trap '/bin/rm -rf "$WORK"' EXIT
APP="$WORK/听见书页安装器.app"

/usr/bin/ditto "$ROOT/installer/apps/听见书页安装器.app" "$APP"
mkdir -p "$APP/Contents/Resources" "$OUTPUT"
/usr/bin/tar -czf "$APP/Contents/Resources/source.tar.gz" \
  --exclude='.git' --exclude='.venv' --exclude='.local' --exclude='node_modules' \
  --exclude='dist' --exclude='playwright-report' --exclude='test-results' \
  -C "$(dirname "$ROOT")" "$(basename "$ROOT")"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUTPUT/听见书页安装器-0.2.0.zip"
/usr/bin/shasum -a 256 "$OUTPUT/听见书页安装器-0.2.0.zip" > "$OUTPUT/听见书页安装器-0.2.0.zip.sha256"
echo "$OUTPUT/听见书页安装器-0.2.0.zip"
