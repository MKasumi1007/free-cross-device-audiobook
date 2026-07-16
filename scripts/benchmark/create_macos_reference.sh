#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
output_dir="$root/.local/benchmark"
text="今天我们用一段合成声音测试免费听书工具。这里不包含用户的真实声音，也不包含任何真实书籍内容。"

mkdir -p "$output_dir"
printf '%s\n' "$text" > "$output_dir/reference.txt"
say -v Tingting -r 175 -o "$output_dir/reference.aiff" "$text"
ffmpeg -hide_banner -loglevel error -y \
  -i "$output_dir/reference.aiff" -ar 24000 -ac 1 \
  "$output_dir/reference.wav"

printf 'Created private local reference: %s\n' "$output_dir/reference.wav"
