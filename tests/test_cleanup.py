from __future__ import annotations

import os
import time
from pathlib import Path

from mac_agent.cleanup import clean_expired_generation_files


def test_cleanup_removes_only_expired_audio_and_keeps_checkpoint(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()
    old_wav = task / "segment.wav"
    old_wav.write_bytes(b"temporary")
    checkpoint = task / "checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    recent = task / "recent.m4a"
    recent.write_bytes(b"recent")
    old = time.time() - 25 * 60 * 60
    os.utime(old_wav, (old, old))

    assert clean_expired_generation_files(tmp_path) == 1
    assert not old_wav.exists()
    assert checkpoint.exists()
    assert recent.exists()
