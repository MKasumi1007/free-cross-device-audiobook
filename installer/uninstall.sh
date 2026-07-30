#!/bin/bash
set -Eeuo pipefail

LABEL="io.github.mkasumi1007.audiobook-mac-agent"
WATCHDOG_LABEL="io.github.mkasumi1007.audiobook-mac-agent-watchdog"
APP_NAME="米兰读书"
LEGACY_DATA_DIRECTORY="听见书页"
DATA_ROOT="${AUDIOBOOK_DATA_ROOT:-$HOME/Library/Application Support/$LEGACY_DATA_DIRECTORY}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WATCHDOG_PLIST="$HOME/Library/LaunchAgents/$WATCHDOG_LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN" "$PLIST" >/dev/null 2>&1 || true
launchctl bootout "$DOMAIN" "$WATCHDOG_PLIST" >/dev/null 2>&1 || true
/bin/rm -f "$PLIST"
/bin/rm -f "$WATCHDOG_PLIST"

for target in \
  "$DATA_ROOT/agent-runtime" "$DATA_ROOT/qwen-runtime" "$DATA_ROOT/mlx-runtime" \
  "$DATA_ROOT/agent-runtime.next" "$DATA_ROOT/qwen-runtime.next" "$DATA_ROOT/mlx-runtime.next" \
  "$DATA_ROOT/agent-runtime.previous" "$DATA_ROOT/qwen-runtime.previous" \
  "$DATA_ROOT/mlx-runtime.previous" \
  "$DATA_ROOT/tools" "$DATA_ROOT/installer"; do
  case "$target" in
    "$DATA_ROOT/agent-runtime"|"$DATA_ROOT/qwen-runtime"|"$DATA_ROOT/mlx-runtime"|\
    "$DATA_ROOT/agent-runtime.next"|"$DATA_ROOT/qwen-runtime.next"|\
    "$DATA_ROOT/mlx-runtime.next"|"$DATA_ROOT/agent-runtime.previous"|\
    "$DATA_ROOT/qwen-runtime.previous"|"$DATA_ROOT/mlx-runtime.previous"|\
    "$DATA_ROOT/tools"|"$DATA_ROOT/installer")
      /bin/rm -rf "$target"
      ;;
    *) echo "拒绝删除非运行组件路径：$target" >&2; exit 1 ;;
  esac
done

/bin/rm -rf "$HOME/Applications/$APP_NAME"
/bin/rm -rf "$HOME/Applications/$LEGACY_DATA_DIRECTORY"
echo "后台服务和运行组件已卸载。"
echo "以下用户数据被完整保留："
echo "  $DATA_ROOT/books"
echo "  $DATA_ROOT/voices"
echo "  $DATA_ROOT/generation"
echo "  $DATA_ROOT/models"
