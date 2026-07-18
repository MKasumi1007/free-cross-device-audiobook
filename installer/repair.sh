#!/bin/bash
set -Eeuo pipefail

ACTION="${1:-runtime}"
DATA_ROOT="${AUDIOBOOK_DATA_ROOT:-$HOME/Library/Application Support/听见书页}"
INSTALLER="$DATA_ROOT/installer/install.sh"

case "$ACTION" in
  qwen|model|launch_agent)
    exec /bin/bash "$INSTALLER" --repair "$ACTION" --source-root "$DATA_ROOT"
    ;;
  runtime)
    exec /bin/bash "$DATA_ROOT/installer/update.sh"
    ;;
  *)
    echo "不支持的修复操作：$ACTION" >&2
    exit 64
    ;;
esac
