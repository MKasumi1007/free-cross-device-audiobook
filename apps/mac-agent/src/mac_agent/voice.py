from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol


class VoiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VoicePicker(Protocol):
    def choose(self) -> Path | None: ...


class CommandRunner(Protocol):
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...


class SubprocessRunner:
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, text=True, check=False)


@dataclass(frozen=True)
class VoiceProfile:
    voice_version: str
    audio_path: str
    transcript: str
    duration_seconds: float
    audio_sha256: str
    created_at: str
    confirmed_at: str | None = None
    preview_path: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.confirmed_at is not None

    def public_status(self) -> dict[str, str | float | bool | None]:
        return {
            "voice_version": self.voice_version,
            "duration_seconds": self.duration_seconds,
            "confirmed": self.confirmed,
            "created_at": self.created_at,
            "confirmed_at": self.confirmed_at,
            "preview_available": bool(self.preview_path and Path(self.preview_path).exists()),
        }


class VoiceRegistry:
    MIN_DURATION_SECONDS = 10.0
    MAX_DURATION_SECONDS = 30.0

    def __init__(
        self,
        root: Path,
        picker: VoicePicker,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.root = root
        self.picker = picker
        self.runner = runner or SubprocessRunner()

    @property
    def profile_path(self) -> Path:
        return self.root / "current.json"

    def choose_and_create(self, transcript: str) -> VoiceProfile | None:
        transcript = transcript.strip()
        if not transcript:
            raise VoiceError("VOICE_TRANSCRIPT_REQUIRED", "请填写与录音完全对应的文字。")
        selected = self.picker.choose()
        if selected is None:
            return None
        if not selected.is_file():
            raise VoiceError("VOICE_FILE_MISSING", "选择的声音文件不存在。")
        return self.create_from_file(selected, transcript)

    def create_from_file(self, source: Path, transcript: str) -> VoiceProfile:
        transcript = transcript.strip()
        if not transcript:
            raise VoiceError("VOICE_TRANSCRIPT_REQUIRED", "请填写与录音完全对应的文字。")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.root / "voice-normalized.tmp.wav"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ]
        result = self.runner.run(command)
        if result.returncode != 0 or not temporary.exists():
            temporary.unlink(missing_ok=True)
            raise VoiceError("VOICE_NORMALIZE_FAILED", "声音文件无法处理，请换一个清晰录音。")

        duration = self._duration(temporary)
        if not self.MIN_DURATION_SECONDS <= duration <= self.MAX_DURATION_SECONDS:
            temporary.unlink(missing_ok=True)
            raise VoiceError("VOICE_DURATION_INVALID", "声音录音需要在 10 到 30 秒之间。")

        audio_hash = self._hash_file(temporary)
        version_hash = sha256(f"{audio_hash}\0{transcript}".encode()).hexdigest()
        voice_version = f"voice-{version_hash[:16]}"
        version_dir = self.root / voice_version
        version_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        normalized = version_dir / "reference.wav"
        os.replace(temporary, normalized)
        normalized.chmod(0o600)
        created_at = datetime.now(UTC).isoformat()
        profile = VoiceProfile(
            voice_version=voice_version,
            audio_path=str(normalized),
            transcript=transcript,
            duration_seconds=duration,
            audio_sha256=audio_hash,
            created_at=created_at,
        )
        self._write(profile)
        return profile

    def load(self) -> VoiceProfile | None:
        if not self.profile_path.exists():
            return None
        try:
            value = json.loads(self.profile_path.read_text(encoding="utf-8"))
            profile = VoiceProfile(**value)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise VoiceError("VOICE_PROFILE_DAMAGED", "本机声音设置已损坏，请重新选择录音。") from error
        audio = Path(profile.audio_path)
        if not audio.is_file() or self._hash_file(audio) != profile.audio_sha256:
            raise VoiceError("VOICE_AUDIO_CHANGED", "声音录音已被移动或更改，请重新选择。")
        return profile

    def record_preview(self, preview_path: Path) -> VoiceProfile:
        profile = self._required()
        if not preview_path.is_file():
            raise VoiceError("VOICE_PREVIEW_MISSING", "试听还没有生成完成。")
        target = Path(profile.audio_path).parent / "preview.m4a"
        os.replace(preview_path, target)
        target.chmod(0o600)
        updated = replace(profile, preview_path=str(target), confirmed_at=None)
        self._write(updated)
        return updated

    def confirm(self, voice_version: str) -> VoiceProfile:
        profile = self._required()
        if profile.voice_version != voice_version:
            raise VoiceError("STALE_VOICE_VERSION", "声音已经更新，请先听新的试听。")
        if not profile.preview_path or not Path(profile.preview_path).is_file():
            raise VoiceError("VOICE_PREVIEW_REQUIRED", "请先生成并听完试听。")
        updated = replace(profile, confirmed_at=datetime.now(UTC).isoformat())
        self._write(updated)
        return updated

    def _required(self) -> VoiceProfile:
        profile = self.load()
        if profile is None:
            raise VoiceError("VOICE_NOT_CONFIGURED", "请先选择你的声音录音。")
        return profile

    def _duration(self, path: Path) -> float:
        result = self.runner.run([
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ])
        try:
            return float(result.stdout.strip())
        except (TypeError, ValueError) as error:
            raise VoiceError("VOICE_DURATION_UNKNOWN", "无法读取声音录音长度。") from error

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _write(self, profile: VoiceProfile) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.profile_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(profile), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, self.profile_path)
