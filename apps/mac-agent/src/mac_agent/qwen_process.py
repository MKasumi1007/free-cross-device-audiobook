from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import IO, Any, Callable, Protocol, cast

from .generation import GeneratedAudio, GenerationError


class WorkerProcess(Protocol):
    stdin: IO[str] | None
    stdout: IO[str] | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[list[str]], WorkerProcess]


def _start_process(command: list[str]) -> WorkerProcess:
    return cast(
        WorkerProcess,
        subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        ),
    )


class QwenProcessGenerator:
    DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"

    def __init__(
        self,
        *,
        python_path: Path,
        worker_script: Path,
        reference_audio: Path,
        reference_text: str,
        model: str = DEFAULT_MODEL,
        idle_seconds: float = 240,
        process_factory: ProcessFactory = _start_process,
    ) -> None:
        self.python_path = python_path
        self.worker_script = worker_script
        self.reference_audio = reference_audio
        self.reference_text = reference_text
        self.model = model
        self.idle_seconds = idle_seconds
        self.process_factory = process_factory
        self._process: WorkerProcess | None = None
        self._idle_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def generate(self, text: str, output_wav: Path) -> GeneratedAudio:
        with self._lock:
            process = self._ensure_process()
            if process.stdin is None or process.stdout is None:
                self._stop_locked()
                raise GenerationError("TTS_WORKER_BROKEN", "语音模型进程无法通信。")
            request = {
                "command": "generate",
                "reference_audio": str(self.reference_audio),
                "reference_text": self.reference_text,
                "text": text,
                "output": str(output_wav),
            }
            try:
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
                response_line = process.stdout.readline()
                response: dict[str, Any] = json.loads(response_line)
            except (BrokenPipeError, OSError, json.JSONDecodeError) as error:
                self._stop_locked()
                raise GenerationError("TTS_WORKER_EXITED", "语音模型意外退出，稍后会从检查点继续。") from error
            if response.get("status") != "ok":
                code = str(response.get("code") or "TTS_GENERATION_FAILED")
                raise GenerationError(code, "这一段语音没有生成成功，稍后会自动重试。")
            self._arm_idle_timer()
            return GeneratedAudio(
                duration_seconds=float(response["duration_seconds"]),
                sample_rate=int(response["sample_rate"]),
            )

    def unload(self) -> None:
        with self._lock:
            self._stop_locked()

    def _ensure_process(self) -> WorkerProcess:
        if self.loaded:
            assert self._process is not None
            return self._process
        command = [
            str(self.python_path),
            str(self.worker_script),
            "--model",
            self.model,
        ]
        self._process = self.process_factory(command)
        return self._process

    def _arm_idle_timer(self) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
        self._idle_timer = threading.Timer(self.idle_seconds, self.unload)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _stop_locked(self) -> None:
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        if process.stdin:
            try:
                process.stdin.write('{"command":"shutdown"}\n')
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
