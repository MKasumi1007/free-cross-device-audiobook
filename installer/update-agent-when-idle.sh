#!/bin/bash
set -Eeuo pipefail

LEGACY_DATA_DIRECTORY="听见书页"
DATA_ROOT="${AUDIOBOOK_DATA_ROOT:-$HOME/Library/Application Support/$LEGACY_DATA_DIRECTORY}"
SOURCE_ROOT="${AUDIOBOOK_SOURCE_ROOT:-}"
POLL_SECONDS="${AUDIOBOOK_IDLE_UPDATE_POLL_SECONDS:-15}"
SETTLE_SECONDS="${AUDIOBOOK_IDLE_UPDATE_SETTLE_SECONDS:-10}"
MARKER_NAME=""

usage() {
  cat <<'EOF'
用法：update-agent-when-idle.sh --source-root <项目目录> [--marker <文件名>]

等待当前语音生成任务释放检查点锁，再安全更新 Mac Agent。该脚本不会更新
Qwen 模型，也不会删除书籍、声音、音频检查点或用户数据。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-root) SOURCE_ROOT="${2:?缺少项目目录}"; shift 2 ;;
    --marker) MARKER_NAME="${2:?缺少标记文件名}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 64 ;;
  esac
done

if [[ -z "$SOURCE_ROOT" || ! -f "$SOURCE_ROOT/pyproject.toml" \
  || ! -f "$SOURCE_ROOT/installer/install.sh" ]]; then
  echo "找不到完整项目目录，请使用 --source-root 指定源码位置。" >&2
  exit 64
fi
if [[ -n "$MARKER_NAME" && ("$MARKER_NAME" == */* || "$MARKER_NAME" == .* ) ]]; then
  echo "--marker 只能使用普通文件名。" >&2
  exit 64
fi
if [[ ! "$POLL_SECONDS" =~ ^[0-9]+$ || ! "$SETTLE_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "等待秒数必须是非负整数。" >&2
  exit 64
fi

LOCK="$DATA_ROOT/generation/active-task.lock"
LOG="$DATA_ROOT/logs/idle-update.log"
STATE_DIR="$DATA_ROOT/state"
MARKER=""
mkdir -p "$DATA_ROOT/generation" "$DATA_ROOT/logs" "$STATE_DIR"
chmod 700 "$DATA_ROOT" "$DATA_ROOT/generation" "$DATA_ROOT/logs" "$STATE_DIR"

if [[ -n "$MARKER_NAME" ]]; then
  MARKER="$STATE_DIR/$MARKER_NAME"
  [[ -e "$MARKER" ]] && exit 0
fi

echo "[$(date -u +%FT%TZ)] 等待当前语音生成任务结束。" >>"$LOG"
while true; do
  while [[ -e "$LOCK" ]]; do
    sleep "$POLL_SECONDS"
  done
  sleep "$SETTLE_SECONDS"
  [[ -e "$LOCK" ]] || break
done

echo "[$(date -u +%FT%TZ)] 开始安全更新 Mac Agent。" >>"$LOG"
/bin/bash "$SOURCE_ROOT/installer/install.sh" \
  --repair runtime \
  --source-root "$SOURCE_ROOT" \
  --skip-model-test >>"$LOG" 2>&1

if [[ -n "$MARKER" ]]; then
  /usr/bin/touch "$MARKER"
  chmod 600 "$MARKER"
fi
echo "[$(date -u +%FT%TZ)] Mac Agent 更新完成。" >>"$LOG"
