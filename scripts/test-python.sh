#!/usr/bin/env bash
set -euo pipefail

if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m pytest -q
fi

exec python3 -m pytest -q
