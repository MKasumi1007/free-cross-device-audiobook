#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s OWNER/REPOSITORY\n' "$0" >&2
  exit 2
fi

repo="$1"
tag="stage0-release-probe"
root="$(cd "$(dirname "$0")/../.." && pwd)"
probe_dir="$root/.local/release-probe"
asset="$probe_dir/public-domain-sine-probe.m4a"
partial="$probe_dir/range-response.bin"
headers="$probe_dir/range-headers.txt"

mkdir -p "$probe_dir"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i 'sine=frequency=440:duration=3' \
  -c:a aac -b:a 96k "$asset"

if ! gh release view "$tag" --repo "$repo" >/dev/null 2>&1; then
  gh release create "$tag" --repo "$repo" --title "Stage 0 public media probe" \
    --notes "Synthetic sine-wave asset for HTTP Range and Safari playback validation."
fi

gh release upload "$tag" "$asset" --repo "$repo" --clobber
asset_url="$(gh release view "$tag" --repo "$repo" \
  --json assets --jq '.assets[] | select(.name == "public-domain-sine-probe.m4a") | .url')"

if [[ -z "$asset_url" ]]; then
  printf 'Could not resolve release asset URL.\n' >&2
  exit 1
fi

curl --fail --silent --show-error --location \
  --header 'Range: bytes=0-1023' \
  --dump-header "$headers" --output "$partial" "$asset_url"

status="$(awk 'toupper($1) ~ /^HTTP/ {code=$2} END {print code}' "$headers")"
bytes="$(wc -c < "$partial" | tr -d ' ')"
sha256="$(shasum -a 256 "$asset" | awk '{print $1}')"

printf 'REPOSITORY=%s\n' "$repo"
printf 'TAG=%s\n' "$tag"
printf 'ASSET_URL=%s\n' "$asset_url"
printf 'RANGE_STATUS=%s\n' "$status"
printf 'RANGE_BYTES=%s\n' "$bytes"
printf 'LOCAL_SHA256=%s\n' "$sha256"

if [[ "$status" != "206" || "$bytes" -ne 1024 ]]; then
  printf 'Release asset did not satisfy the expected 206/1024-byte Range response.\n' >&2
  exit 1
fi
