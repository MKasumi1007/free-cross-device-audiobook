from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Protocol

from .generation import SingleTaskLock
from .error_reporting import AgentOperationError, completed_process_details, reporter
from .paths import (
    DEFAULT_MLX_MODEL,
    DEFAULT_QWEN_MODEL,
    logs_root,
    mlx_python,
    qwen_python,
)
from .qwen_process import QwenProcessGenerator
from .voice import VoiceError, VoiceProfile, VoiceRegistry


PREVIEW_TEXT = (
    "雨停之后，窗外的树叶被洗得清亮。我们把书轻轻翻开，从这一页开始，慢慢听见文字里的呼吸。"
    "有些故事适合在清晨读，有些故事适合在夜晚听。声音不必急着赶路，只要把每一句话说清楚，"
    "让停顿自然，让语气温和。远处的风穿过屋檐，茶汤还带着暖意，书中的人物正沿着旧日的小径走来。"
    "当你再次打开这本书，听书工具会记得上次停留的位置，也会继续准备后面的篇章。"
    "这段试听只保存在你的电脑里，用来确认声音、节奏和语气是否合适。"
)


class PreviewGeneratorFactory(Protocol):
    def __call__(self, profile: VoiceProfile) -> QwenProcessGenerator: ...


class PreviewResourcePolicy(Protocol):
    def pause_reason(self) -> str | None: ...


class VoicePreviewService:
    def __init__(
        self,
        registry: VoiceRegistry,
        generator_factory: PreviewGeneratorFactory,
        *,
        policy: PreviewResourcePolicy | None = None,
        lock_path: Path | None = None,
        generation_model_loaded: Callable[[], bool] | None = None,
    ) -> None:
        self.registry = registry
        self.generator_factory = generator_factory
        self.policy = policy
        self.lock_path = lock_path
        self.generation_model_loaded = generation_model_loaded or (lambda: False)
        self._state = "IDLE"
        self._error = ""
        self._lock = threading.Lock()
        self._generator: QwenProcessGenerator | None = None

    def status(self) -> dict[str, str | bool]:
        profile = self.registry.load()
        with self._lock:
            state = self._state
            error = self._error
        if profile and profile.preview_path and Path(profile.preview_path).is_file() and state == "IDLE":
            state = "READY"
        return {
            "state": state,
            "error": error,
            "model_loaded": bool(self._generator and self._generator.loaded),
        }

    def start(self) -> dict[str, str | bool]:
        profile = self.registry.load()
        if profile is None:
            raise VoiceError("VOICE_NOT_CONFIGURED", "请先选择你的声音录音。")
        with self._lock:
            if self._state == "GENERATING":
                return {
                    "state": "GENERATING",
                    "error": self._error,
                    "model_loaded": bool(self._generator and self._generator.loaded),
                }
            self._state = "GENERATING"
            self._error = ""
        thread = threading.Thread(target=self._generate, args=(profile,), daemon=True)
        thread.start()
        return self.status()

    def unload(self) -> None:
        generator = self._generator
        self._generator = None
        if generator:
            generator.unload()

    def _generate(self, profile: VoiceProfile) -> None:
        private_dir = Path(profile.audio_path).parent
        wav = private_dir / "preview.tmp.wav"
        m4a = private_dir / "preview.tmp.m4a"
        try:
            if self.policy and self.policy.pause_reason():
                raise VoiceError("PREVIEW_RESOURCE_GUARD", "电脑资源不足，试听稍后自动再试。")
            if self.generation_model_loaded():
                raise VoiceError("TTS_BUSY", "正在生成听书音频，请稍后再试听。")
            if self.lock_path is None:
                self._generate_locked(profile, wav, m4a)
            else:
                with SingleTaskLock(self.lock_path):
                    if self.generation_model_loaded():
                        raise VoiceError("TTS_BUSY", "正在生成听书音频，请稍后再试听。")
                    self._generate_locked(profile, wav, m4a)
            with self._lock:
                self._state = "READY"
                self._error = ""
        except Exception as error:
            reporter.record("voice.preview", error, code=getattr(error, "code", "VOICE_PREVIEW_FAILED"))
            with self._lock:
                self._state = "FAILED"
                self._error = (
                    error.user_message
                    if isinstance(error, AgentOperationError)
                    else str(error) if isinstance(error, VoiceError)
                    else "试听没有生成完成，完整原因已写入本机日志。"
                )
        finally:
            self.unload()
            wav.unlink(missing_ok=True)
            m4a.unlink(missing_ok=True)
            try:
                os.sync()
            except AttributeError:
                pass

    def _generate_locked(self, profile: VoiceProfile, wav: Path, m4a: Path) -> None:
        self.unload()
        generator = self.generator_factory(profile)
        self._generator = generator
        generator.generate(PREVIEW_TEXT, wav)
        try:
            encoded = subprocess.run(
                [
                    "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wav),
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                "-movflags",
                "+faststart",
                str(m4a),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as error:
            raise VoiceError("FFMPEG_MISSING", "没有找到 FFmpeg，请在系统状态中运行自动修复。") from error
        if encoded.returncode != 0 or not m4a.is_file():
            raise AgentOperationError(
                "FFMPEG_ENCODING_FAILED",
                "试听编码失败，完整原因已写入本机日志。",
                details=completed_process_details(encoded),
            )
        self.registry.record_preview(m4a)


def default_qwen_factory(profile: VoiceProfile) -> QwenProcessGenerator:
    backend = os.environ.get("AUDIOBOOK_TTS_BACKEND", "qwen").strip().lower()
    if backend == "mlx":
        python_path = mlx_python()
        if not python_path.is_file():
            raise VoiceError("MLX_ENV_MISSING", "本机 MLX 声音模型环境尚未准备好。")
        try:
            batch_size = max(
                1,
                min(2, int(os.environ.get("AUDIOBOOK_TTS_BATCH_SIZE", "2"))),
            )
        except ValueError:
            batch_size = 2
        return QwenProcessGenerator(
            python_path=python_path,
            worker_script=Path(__file__).with_name("mlx_worker.py"),
            reference_audio=Path(profile.audio_path),
            reference_text=profile.transcript,
            model=os.environ.get("AUDIOBOOK_MLX_MODEL", DEFAULT_MLX_MODEL),
            stderr_path=logs_root() / "mlx-stderr.log",
            batch_size=batch_size,
            backend_name="mlx",
        )
    if backend != "qwen":
        raise VoiceError("TTS_BACKEND_INVALID", "声音引擎设置无效，请运行自动修复。")

    python_path = qwen_python()
    if not python_path.is_file():
        raise VoiceError("QWEN_ENV_MISSING", "本机声音模型环境尚未准备好。")
    return QwenProcessGenerator(
        python_path=python_path,
        worker_script=Path(__file__).with_name("qwen_worker.py"),
        reference_audio=Path(profile.audio_path),
        reference_text=profile.transcript,
        model=os.environ.get("AUDIOBOOK_QWEN_MODEL", DEFAULT_QWEN_MODEL),
        stderr_path=logs_root() / "qwen-stderr.log",
        backend_name="qwen",
    )
