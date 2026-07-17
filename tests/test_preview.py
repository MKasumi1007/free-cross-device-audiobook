from __future__ import annotations

from pathlib import Path

from mac_agent.preview import VoicePreviewService
from mac_agent.voice import VoiceProfile


class FakeRegistry:
    def __init__(self, profile: VoiceProfile) -> None:
        self.profile = profile

    def load(self) -> VoiceProfile:
        return self.profile


class MemoryPressurePolicy:
    def pause_reason(self) -> str:
        return "MEMORY_PRESSURE"


def test_preview_never_loads_qwen_under_memory_pressure(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"private voice")
    profile = VoiceProfile(
        voice_version="voice-test",
        audio_path=str(reference),
        transcript="仅本机文字",
        duration_seconds=12,
        audio_sha256="a" * 64,
        created_at="2026-07-17T00:00:00Z",
    )
    factory_calls = 0

    def factory(_profile: VoiceProfile) -> object:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("Qwen must not load")

    service = VoicePreviewService(
        FakeRegistry(profile),  # type: ignore[arg-type]
        factory,  # type: ignore[arg-type]
        policy=MemoryPressurePolicy(),
        lock_path=tmp_path / "active-task.lock",
    )
    service._generate(profile)

    assert factory_calls == 0
    assert service.status()["state"] == "FAILED"
    assert service.status()["model_loaded"] is False
