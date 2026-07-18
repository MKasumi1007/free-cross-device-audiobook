from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mac_agent.media import duration_from_ffmpeg_progress, ffmpeg_duration_command


def test_ffmpeg_duration_uses_decode_progress_without_ffprobe(tmp_path: Path) -> None:
    audio = tmp_path / "audio.m4a"
    command = ffmpeg_duration_command(audio)
    assert command[0] == "ffmpeg"
    assert "-progress" in command
    assert "ffprobe" not in command
    result = subprocess.CompletedProcess(
        command,
        0,
        "out_time_us=3680000\nout_time=00:00:03.680000\nprogress=end\n",
        "",
    )
    assert duration_from_ffmpeg_progress(result) == pytest.approx(3.68)


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess(["ffmpeg"], 1, "", "decode failed"),
        subprocess.CompletedProcess(["ffmpeg"], 0, "out_time_us=0\n", ""),
    ],
)
def test_ffmpeg_duration_rejects_failed_or_empty_audio(
    result: subprocess.CompletedProcess[str],
) -> None:
    with pytest.raises(ValueError):
        duration_from_ffmpeg_progress(result)
