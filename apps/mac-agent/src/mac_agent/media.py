from __future__ import annotations

import subprocess
from pathlib import Path


def ffmpeg_duration_command(path: Path) -> list[str]:
    """Decode the first audio stream and report its exact processed duration."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-f",
        "null",
        "-",
        "-progress",
        "pipe:1",
        "-nostats",
    ]


def duration_from_ffmpeg_progress(result: subprocess.CompletedProcess[str]) -> float:
    if result.returncode != 0:
        raise ValueError("FFmpeg could not decode the audio stream")
    values: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    raw_microseconds = values.get("out_time_us") or values.get("out_time_ms")
    if raw_microseconds:
        duration = int(raw_microseconds) / 1_000_000
    else:
        hours, minutes, seconds = values.get("out_time", "0:0:0").split(":")
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if duration <= 0:
        raise ValueError("FFmpeg reported a non-positive duration")
    return duration
