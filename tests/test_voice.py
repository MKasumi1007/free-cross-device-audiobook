from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mac_agent.voice import VoiceError, VoiceRegistry


class FakePicker:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def choose(self) -> Path | None:
        return self.path


class FakeRunner:
    def __init__(self, duration: float = 15.0) -> None:
        self.duration = duration
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if "-progress" in command:
            microseconds = int(self.duration * 1_000_000)
            return subprocess.CompletedProcess(
                command,
                0,
                f"out_time_us={microseconds}\nprogress=end\n",
                "",
            )
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"private normalized voice")
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected command: {command}")


def test_voice_is_normalized_versioned_and_requires_preview_before_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "recording.m4a"
    source.write_bytes(b"not committed")
    runner = FakeRunner()
    registry = VoiceRegistry(tmp_path / "private-voices", FakePicker(source), runner=runner)

    profile = registry.choose_and_create("这段文字与录音完全一致。")
    assert profile is not None
    assert profile.voice_version.startswith("voice-")
    assert profile.confirmed is False
    assert Path(profile.audio_path).read_bytes() == b"private normalized voice"
    assert runner.commands[0][0] == "ffmpeg"
    assert "-ac" in runner.commands[0] and "-ar" in runner.commands[0]

    with pytest.raises(VoiceError) as error:
        registry.confirm(profile.voice_version)
    assert error.value.code == "VOICE_PREVIEW_REQUIRED"

    preview = tmp_path / "preview.tmp.m4a"
    preview.write_bytes(b"generated preview")
    with_preview = registry.record_preview(preview)
    confirmed = registry.confirm(with_preview.voice_version)
    assert confirmed.confirmed is True
    assert "transcript" not in confirmed.public_status()
    assert "audio_path" not in confirmed.public_status()


def test_new_voice_version_invalidates_previous_confirmation(tmp_path: Path) -> None:
    first = tmp_path / "first.m4a"
    first.write_bytes(b"first")
    runner = FakeRunner()
    registry = VoiceRegistry(tmp_path / "voices", FakePicker(first), runner=runner)
    profile = registry.choose_and_create("第一段准确文字")
    assert profile is not None
    preview = tmp_path / "first-preview.m4a"
    preview.write_bytes(b"preview")
    registry.record_preview(preview)
    registry.confirm(profile.voice_version)

    second = tmp_path / "second.m4a"
    second.write_bytes(b"second")
    runner.duration = 16.0
    profile2 = registry.create_from_file(second, "第二段不同文字")
    assert profile2.voice_version != profile.voice_version
    assert profile2.confirmed is False


@pytest.mark.parametrize("duration", [9.9, 30.1])
def test_rejects_voice_outside_ten_to_thirty_seconds(tmp_path: Path, duration: float) -> None:
    source = tmp_path / "voice.m4a"
    source.write_bytes(b"voice")
    registry = VoiceRegistry(tmp_path / "voices", FakePicker(source), runner=FakeRunner(duration))
    with pytest.raises(VoiceError) as error:
        registry.choose_and_create("对应文字")
    assert error.value.code == "VOICE_DURATION_INVALID"
    assert not (tmp_path / "voices" / "voice-normalized.tmp.wav").exists()
