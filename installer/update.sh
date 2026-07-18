#!/bin/bash
set -Eeuo pipefail

REPOSITORY="MKasumi1007/free-cross-device-audiobook"
DATA_ROOT="${AUDIOBOOK_DATA_ROOT:-$HOME/Library/Application Support/听见书页}"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/audiobook-update.XXXXXX")"
trap '/bin/rm -rf "$TEMP_DIR"' EXIT

/usr/bin/curl --fail --location --silent --show-error \
  "https://codeload.github.com/$REPOSITORY/tar.gz/refs/heads/main" \
  -o "$TEMP_DIR/source.tar.gz"
/usr/bin/tar -xzf "$TEMP_DIR/source.tar.gz" -C "$TEMP_DIR"
SOURCE_ROOT="$(find "$TEMP_DIR" -maxdepth 1 -type d -name 'free-cross-device-audiobook-*' -print -quit)"
[[ -n "$SOURCE_ROOT" && -f "$SOURCE_ROOT/pyproject.toml" ]] || {
  echo "下载的更新包不完整。" >&2
  exit 1
}
exec /bin/bash "$SOURCE_ROOT/installer/install.sh" --source-root "$SOURCE_ROOT"
