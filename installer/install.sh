#!/bin/bash
set -Eeuo pipefail

APP_NAME="米兰读书"
LEGACY_DATA_DIRECTORY="听见书页"
APP_VERSION="0.4.0"
UV_VERSION="0.11.29"
FFMPEG_VERSION="7.1"
IMAGEIO_FFMPEG_VERSION="0.6.0"
FFMPEG_WHEEL_SHA256="b1ae3173414b5fc5f538a726c4e48ea97edc0d2cdc11f103afee655c463fa742"
FFMPEG_BINARY_SHA256="6d175a4743ca50256e89a8cdd731100f9cee33bd79aeea46894d209410dc6617"
FFMPEG_WHEEL_URL="https://files.pythonhosted.org/packages/40/5c/f3d8a657d362cc93b81aab8feda487317da5b5d31c0e1fdfd5e986e55d17/imageio_ffmpeg-0.6.0-py3-none-macosx_11_0_arm64.whl"
MODEL_REPOSITORY="models--Qwen--Qwen3-TTS-12Hz-0.6B-Base"
DEFAULT_DATA_ROOT="$HOME/Library/Application Support/$LEGACY_DATA_DIRECTORY"
DATA_ROOT="${AUDIOBOOK_DATA_ROOT:-$DEFAULT_DATA_ROOT}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ACTION="full"
SKIP_MODEL_TEST=0
SWAPPED=0
INSTALL_COMMITTED=0
AGENT_RUNTIME_SWAPPED=0
QWEN_RUNTIME_SWAPPED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repair) ACTION="${2:?missing repair action}"; shift 2 ;;
    --source-root) SOURCE_ROOT="$(cd "${2:?missing source root}" && pwd)"; shift 2 ;;
    --skip-model-test) SKIP_MODEL_TEST=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 64 ;;
  esac
done

RESOURCE_DIR="$SOURCE_ROOT/installer"
if [[ ! -f "$RESOURCE_DIR/requirements-qwen.lock" ]]; then
  RESOURCE_DIR="$SCRIPT_DIR"
fi

LOGS="$DATA_ROOT/logs"
mkdir -p "$LOGS" "$DATA_ROOT/books" "$DATA_ROOT/voices" "$DATA_ROOT/generation" \
  "$DATA_ROOT/models/huggingface/hub" "$DATA_ROOT/state" "$DATA_ROOT/tools" "$DATA_ROOT/installer"
chmod 700 "$DATA_ROOT" "$LOGS" "$DATA_ROOT/books" "$DATA_ROOT/voices" \
  "$DATA_ROOT/generation" "$DATA_ROOT/models" "$DATA_ROOT/state" "$DATA_ROOT/tools" "$DATA_ROOT/installer"
INSTALL_LOG="$LOGS/install-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$INSTALL_LOG") 2>&1

echo "[$(date -u +%FT%TZ)] 开始安装 ${APP_NAME} ${APP_VERSION}（${ACTION}）"

fail() {
  if [[ "$SWAPPED" -eq 1 && "$INSTALL_COMMITTED" -eq 0 ]] \
    && declare -F rollback_and_restart_agent >/dev/null 2>&1; then
    echo "安装未提交，正在恢复上一套运行环境。" >&2
    trap - ERR
    set +e
    rollback_and_restart_agent
    set -e
    SWAPPED=0
  fi
  echo "安装失败：$*" >&2
  echo "完整日志：$INSTALL_LOG" >&2
  exit 1
}

unexpected_error() {
  local status="$?"
  trap - ERR
  fail "安装命令意外退出（状态 $status）。"
}

trap unexpected_error ERR

safe_remove_runtime() {
  local target="$1"
  case "$target" in
    "$DATA_ROOT/agent-runtime"|"$DATA_ROOT/agent-runtime.next"|"$DATA_ROOT/agent-runtime.previous"|\
    "$DATA_ROOT/qwen-runtime"|"$DATA_ROOT/qwen-runtime.next"|"$DATA_ROOT/qwen-runtime.previous")
      /bin/rm -rf "$target"
      ;;
    *) fail "拒绝删除非运行时路径：$target" ;;
  esac
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "安装器只支持 macOS。"
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  fail "当前正式 Qwen 安装器只支持 Apple Silicon Mac。"
fi
if [[ ! -f "$RESOURCE_DIR/requirements-agent.lock" || ! -f "$RESOURCE_DIR/model_self_test.py" ]]; then
  fail "安装包不完整，请重新下载。"
fi

FREE_KB="$(df -Pk "$DATA_ROOT" | awk 'NR==2 {print $4}')"
if [[ "$ACTION" != "launch_agent" && "$FREE_KB" -lt 6291456 ]]; then
  fail "剩余磁盘不足 6 GiB；请先腾出空间再安装，书架和声音数据不会被删除。"
fi

TOOLS="$DATA_ROOT/tools"
UV="$TOOLS/uv-$UV_VERSION"
if [[ ! -x "$UV" ]]; then
  ARCHIVE="uv-aarch64-apple-darwin.tar.gz"
  TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/audiobook-uv.XXXXXX")"
  trap '/bin/rm -rf "$TEMP_DIR"' EXIT
  BASE_URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION"
  /usr/bin/curl --fail --location --silent --show-error "$BASE_URL/$ARCHIVE" -o "$TEMP_DIR/$ARCHIVE"
  /usr/bin/curl --fail --location --silent --show-error "$BASE_URL/$ARCHIVE.sha256" -o "$TEMP_DIR/$ARCHIVE.sha256"
  EXPECTED="$(awk '{print $1}' "$TEMP_DIR/$ARCHIVE.sha256")"
  ACTUAL="$(/usr/bin/shasum -a 256 "$TEMP_DIR/$ARCHIVE" | awk '{print $1}')"
  [[ "$EXPECTED" == "$ACTUAL" ]] || fail "uv 下载校验失败。"
  /usr/bin/tar -xzf "$TEMP_DIR/$ARCHIVE" -C "$TEMP_DIR"
  /usr/bin/install -m 755 "$TEMP_DIR/uv-aarch64-apple-darwin/uv" "$UV"
  /bin/rm -rf "$TEMP_DIR"
  trap - EXIT
fi

export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_CACHE_DIR="$TOOLS/uv-cache"
export UV_LINK_MODE=copy
export HF_HOME="$DATA_ROOT/models/huggingface"
export AUDIOBOOK_DATA_ROOT="$DATA_ROOT"
export AUDIOBOOK_QWEN_PYTHON="$DATA_ROOT/qwen-runtime/bin/python"
export AUDIOBOOK_QWEN_MODEL="Qwen/Qwen3-TTS-12Hz-0.6B-Base"

"$UV" python install 3.12

FFMPEG="$(command -v ffmpeg || true)"
if [[ -z "$FFMPEG" ]]; then
  AUDIO_TOOLS="$TOOLS/ffmpeg-$FFMPEG_VERSION-imageio-$IMAGEIO_FFMPEG_VERSION"
  mkdir -p "$AUDIO_TOOLS"
  chmod 700 "$AUDIO_TOOLS"
  TARGET="$AUDIO_TOOLS/ffmpeg"
  if [[ ! -x "$TARGET" || "$(/usr/bin/shasum -a 256 "$TARGET" | awk '{print $1}')" != "$FFMPEG_BINARY_SHA256" ]]; then
    echo "没有找到 FFmpeg，正在下载并校验 Apple Silicon 独立工具。"
    TEMP_FFMPEG="$(mktemp -d "${TMPDIR:-/tmp}/audiobook-ffmpeg.XXXXXX")"
    trap '/bin/rm -rf "$TEMP_FFMPEG"' EXIT
    WHEEL="$TEMP_FFMPEG/imageio-ffmpeg.whl"
    /usr/bin/curl --fail --location --silent --show-error "$FFMPEG_WHEEL_URL" -o "$WHEEL"
    ACTUAL="$(/usr/bin/shasum -a 256 "$WHEEL" | awk '{print $1}')"
    [[ "$ACTUAL" == "$FFMPEG_WHEEL_SHA256" ]] || fail "FFmpeg 下载校验失败。"
    /usr/bin/ditto -x -k "$WHEEL" "$TEMP_FFMPEG/unpacked"
    EXTRACTED="$TEMP_FFMPEG/unpacked/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v$FFMPEG_VERSION"
    [[ -f "$EXTRACTED" ]] || fail "FFmpeg 下载包结构不正确。"
    ACTUAL_BINARY="$(/usr/bin/shasum -a 256 "$EXTRACTED" | awk '{print $1}')"
    [[ "$ACTUAL_BINARY" == "$FFMPEG_BINARY_SHA256" ]] || fail "FFmpeg 二进制校验失败。"
    /usr/bin/install -m 755 "$EXTRACTED" "$TARGET"
    /bin/rm -rf "$TEMP_FFMPEG"
    trap - EXIT
  fi
  /bin/cp "$RESOURCE_DIR/FFMPEG-NOTICE.txt" "$AUDIO_TOOLS/NOTICE.txt"
  chmod 600 "$AUDIO_TOOLS/NOTICE.txt"
  FFMPEG="$AUDIO_TOOLS/ffmpeg"
fi
[[ -x "$FFMPEG" ]] || fail "没有找到 FFmpeg。请安装 FFmpeg 后重新双击安装器。"

build_agent() {
  [[ -f "$SOURCE_ROOT/pyproject.toml" ]] || fail "找不到项目源代码，无法安全更新 Agent。"
  local next="$DATA_ROOT/agent-runtime.next"
  safe_remove_runtime "$next"
  "$UV" venv --python 3.12 "$next"
  "$UV" pip sync --python "$next/bin/python" "$RESOURCE_DIR/requirements-agent.lock"
  "$UV" pip install --python "$next/bin/python" --no-deps "$SOURCE_ROOT"
  "$next/bin/python" -c "import mac_agent, audiobook_core"
}

build_qwen() {
  local next="$DATA_ROOT/qwen-runtime.next"
  safe_remove_runtime "$next"
  "$UV" venv --python 3.12 "$next"
  "$UV" pip sync --python "$next/bin/python" "$RESOURCE_DIR/requirements-qwen.lock"
  "$next/bin/python" -c "import torch, soundfile, qwen_tts; assert torch.__version__.startswith('2.8.')"

  local formal_model="$DATA_ROOT/models/huggingface/hub/$MODEL_REPOSITORY"
  local legacy_model="$HOME/.cache/huggingface/hub/$MODEL_REPOSITORY"
  if [[ ! -d "$formal_model" && -d "$legacy_model" ]]; then
    echo "正在把已有模型迁入正式目录（优先使用 APFS 克隆，不重复占用空间）。"
    if cp -cR "$legacy_model" "$formal_model" 2>/dev/null; then
      :
    else
      /usr/bin/ditto "$legacy_model" "$formal_model"
    fi
  fi

  if [[ "$SKIP_MODEL_TEST" -eq 0 ]]; then
    "$next/bin/python" "$RESOURCE_DIR/model_self_test.py" \
      --data-root "$DATA_ROOT" --ffmpeg "$FFMPEG"
  fi
}

swap_runtime() {
  local name="$1"
  local current="$DATA_ROOT/$name"
  local next="$DATA_ROOT/$name.next"
  local previous="$DATA_ROOT/$name.previous"
  safe_remove_runtime "$previous"
  if [[ -d "$current" ]]; then /bin/mv "$current" "$previous"; fi
  /bin/mv "$next" "$current"
  if [[ "$name" == "agent-runtime" ]]; then
    AGENT_RUNTIME_SWAPPED=1
  else
    QWEN_RUNTIME_SWAPPED=1
  fi
  SWAPPED=1
}

rollback_runtime() {
  local name="$1"
  local current="$DATA_ROOT/$name"
  local previous="$DATA_ROOT/$name.previous"
  if [[ -d "$previous" ]]; then
    safe_remove_runtime "$current"
    /bin/mv "$previous" "$current"
  else
    safe_remove_runtime "$current"
  fi
}

rollback_and_restart_agent() {
  launchctl bootout "gui/$(id -u)/io.github.mkasumi1007.audiobook-mac-agent" \
    >/dev/null 2>&1 || true
  /bin/sleep 1
  if [[ "$QWEN_RUNTIME_SWAPPED" -eq 1 ]]; then rollback_runtime "qwen-runtime"; fi
  if [[ "$AGENT_RUNTIME_SWAPPED" -eq 1 ]]; then rollback_runtime "agent-runtime"; fi
  if [[ -x "$DATA_ROOT/agent-runtime/bin/python" ]]; then
    "$DATA_ROOT/agent-runtime/bin/python" -m mac_agent.launchd || true
  fi
}

if [[ "$ACTION" == "full" || "$ACTION" == "runtime" ]]; then
  build_agent
fi
if [[ "$ACTION" == "full" || "$ACTION" == "qwen" ]]; then
  build_qwen
fi
if [[ "$ACTION" == "model" ]]; then
  [[ -x "$DATA_ROOT/qwen-runtime/bin/python" ]] || fail "Qwen 环境不存在，请先修复 Qwen。"
  "$DATA_ROOT/qwen-runtime/bin/python" "$RESOURCE_DIR/model_self_test.py" \
    --data-root "$DATA_ROOT" --ffmpeg "$FFMPEG"
fi

if [[ -d "$DATA_ROOT/agent-runtime.next" ]]; then swap_runtime "agent-runtime"; fi
if [[ -d "$DATA_ROOT/qwen-runtime.next" ]]; then swap_runtime "qwen-runtime"; fi

if [[ "$ACTION" == "full" || "$ACTION" == "runtime" ]]; then
  /bin/cp "$SOURCE_ROOT/config/firebase-public-config.json" "$DATA_ROOT/firebase-public-config.json"
  chmod 600 "$DATA_ROOT/firebase-public-config.json"
fi

for item in install.sh update.sh update-agent-when-idle.sh uninstall.sh repair.sh model_self_test.py requirements-agent.lock requirements-qwen.lock; do
  SOURCE_ITEM="$RESOURCE_DIR/$item"
  TARGET_ITEM="$DATA_ROOT/installer/$item"
  if [[ "$SOURCE_ITEM" != "$TARGET_ITEM" ]]; then /bin/cp "$SOURCE_ITEM" "$TARGET_ITEM"; fi
done
chmod 700 "$DATA_ROOT/installer/"*.sh "$DATA_ROOT/installer/model_self_test.py"
chmod 600 "$DATA_ROOT/installer/requirements-"*.lock

if [[ "$ACTION" == "full" || "$ACTION" == "runtime" || "$ACTION" == "launch_agent" ]]; then
  [[ -x "$DATA_ROOT/agent-runtime/bin/python" ]] || fail "Agent 运行时不存在。"
  if ! "$DATA_ROOT/agent-runtime/bin/python" -m mac_agent.launchd; then
    fail "新 Agent 没有成功注册为登录启动项，已经回滚到旧运行环境。"
  fi
fi

if [[ -d "$SOURCE_ROOT/installer/apps" ]]; then
  APP_TARGET="$HOME/Applications/$APP_NAME"
  mkdir -p "$APP_TARGET"
  for app in "$SOURCE_ROOT/installer/apps/米兰读书.app" "$SOURCE_ROOT/installer/apps/更新米兰读书.app" "$SOURCE_ROOT/installer/apps/卸载米兰读书.app"; do
    [[ -d "$app" ]] && /usr/bin/ditto "$app" "$APP_TARGET/$(basename "$app")"
  done
  # The old folder contains shortcuts only; user data remains in Application Support.
  /bin/rm -rf "$HOME/Applications/$LEGACY_DATA_DIRECTORY"
fi

if [[ "$ACTION" != "model" && "$ACTION" != "qwen" ]]; then
  HEALTH_OK=0
  for _attempt in 1 2 3 4 5 6 7 8 9 10; do
    HEALTH_RESPONSE="$(/usr/bin/curl --fail --silent --show-error \
      -H "Origin: https://mkasumi1007.github.io" \
      "http://127.0.0.1:17832/v1/health" || true)"
    if [[ "$HEALTH_RESPONSE" == *"\"version\":\"$APP_VERSION\""* ]]; then
      HEALTH_OK=1
      break
    fi
    sleep 1
  done
  if [[ "$HEALTH_OK" -ne 1 ]]; then
    fail "新 Agent 健康检查没有通过，已经回滚到旧运行环境。"
  fi
fi

if [[ "$ACTION" == "qwen" ]]; then
  launchctl kickstart -k "gui/$(id -u)/io.github.mkasumi1007.audiobook-mac-agent" >/dev/null 2>&1 || true
fi

printf '{"version":"%s","installed_at":"%s","ffmpeg":"%s"}\n' \
  "$APP_VERSION" "$(date -u +%FT%TZ)" "$FFMPEG" > "$DATA_ROOT/state/install.json"
chmod 600 "$DATA_ROOT/state/install.json"
INSTALL_COMMITTED=1
AGENT_RUNTIME_SWAPPED=0
QWEN_RUNTIME_SWAPPED=0
safe_remove_runtime "$DATA_ROOT/agent-runtime.previous"
safe_remove_runtime "$DATA_ROOT/qwen-runtime.previous"

echo "安装成功。正式运行目录：$DATA_ROOT"
echo "书架和声音目录已保留：$DATA_ROOT/books；$DATA_ROOT/voices"
