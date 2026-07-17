from __future__ import annotations

import time
from pathlib import Path


TEMPORARY_SUFFIXES = (".wav", ".m4a", ".json.gz", ".tmp", ".tmp.wav", ".tmp.m4a")


def clean_expired_generation_files(root: Path, *, max_age_seconds: int = 24 * 60 * 60) -> int:
    if not root.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.name in {"checkpoint.json", "published.json"}:
            continue
        if not path.name.endswith(TEMPORARY_SUFFIXES):
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed
