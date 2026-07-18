from __future__ import annotations

import json
import os
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


def _start_process(command: list[str], stderr_path: Path) -> WorkerProcess:
    stderr_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with stderr_path.open("a", encoding="utf-8") as error_log:
        return cast(
            WorkerProcess,
            subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=error_log,
                text=True,
                bufsize=1,
                env=os.environ.copy(),
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
        process_factory: ProcessFactory | None = None,
        stderr_path: Path | None = None,
    ) -> None:
        self.python_path = python_path
        self.worker_script = worker_script
        self.reference_audio = reference_audio
        self.reference_text = reference_text
        self.model = model
        self.idle_seconds = idle_seconds
        self.stderr_path = stderr_path or worker_script.parent / "qwen-stderr.log"
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
                if not response_line:
                    exit_code = process.poll()
                    self._stop_locked()
                    raise GenerationError(
                        "TTS_WORKER_EXITED",
                        "语音模型进程意外退出，稍后会从检查点继续。",
                        details={
                            "exit_code": exit_code,
                            "python_path": str(self.python_path),
                            "model": self.model,
                            "stderr_path": str(self.stderr_path),
                            "stderr_tail": self._stderr_tail(),
                        },
                    )
                response: dict[str, Any] = json.loads(response_line)
            except GenerationError:
                raise
            except (BrokenPipeError, OSError, json.JSONDecodeError) as error:
                self._stop_locked()
                raise GenerationError(
                    "TTS_WORKER_EXITED",
                    "语音模型意外退出，稍后会从检查点继续。",
                    details={
                        "python_path": str(self.python_path),
                        "model": self.model,
                        "response": response_line if "response_line" in locals() else "",
                        "stderr_path": str(self.stderr_path),
                        "stderr_tail": self._stderr_tail(),
                    },
                ) from error
            if response.get("status") != "ok":
                code = str(response.get("code") or "TTS_GENERATION_FAILED")
                details = {
                    "python_path": str(self.python_path),
                    "model": self.model,
                    "worker_error_type": response.get("error_type"),
                    "worker_error": response.get("error"),
                    "worker_traceback": response.get("traceback"),
                    "stderr_path": str(self.stderr_path),
                    "stderr_tail": self._stderr_tail(),
                }
                self._stop_locked()
                raise GenerationError(
                    code,
                    self._user_message(code),
                    details=details,
                )
            self._arm_idle_timer()
            try:
                return GeneratedAudio(
                    duration_seconds=float(response["duration_seconds"]),
                    sample_rate=int(response["sample_rate"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                self._stop_locked()
                raise GenerationError(
                    "TTS_WORKER_BROKEN",
                    "语音模型返回了无法识别的结果，完整响应已写入本机日志。",
                    details={"response": response, "stderr_tail": self._stderr_tail()},
                ) from error

    def unload(self) -> None:
        with self._lock:
            self._stop_locked()

    def _ensure_process(self) -> WorkerProcess:
        if self.loaded:
            assert self._process is not None
            return self._process
        if not self.python_path.is_file() and self.process_factory is None:
            raise GenerationError(
                "QWEN_ENV_MISSING",
                "Qwen 运行环境不存在，请在系统状态中运行自动修复。",
                details={"python_path": str(self.python_path)},
            )
        if not self.worker_script.is_file() and self.process_factory is None:
            raise GenerationError(
                "QWEN_WORKER_MISSING",
                "语音生成组件不完整，请重新安装。",
                details={"worker_script": str(self.worker_script)},
            )
        command = [
            str(self.python_path),
            str(self.worker_script),
            "--model",
            self.model,
        ]
        self._process = (
            self.process_factory(command)
            if self.process_factory
            else _start_process(command, self.stderr_path)
        )
        return self._process

    def _stderr_tail(self) -> str:
        try:
            return self.stderr_path.read_text(encoding="utf-8", errors="replace")[-12_000:]
        except OSError:
            return ""

    @staticmethod
    def _user_message(code: str) -> str:
        messages = {
            "QWEN_DEPENDENCY_MISSING": "Qwen 依赖不完整，请在系统状态中运行自动修复。",
            "MODEL_NOT_FOUND": "Qwen 模型尚未下载，请在系统状态中运行自动修复。",
            "MODEL_LOAD_FAILED": "Qwen 模型加载失败，完整原因已写入本机日志。",
            "MPS_UNAVAILABLE": "Apple MPS 不可用，已停止本次生成以避免异常。",
            "OUT_OF_MEMORY": "可用内存不足，关闭其他大型应用后会自动重试。",
            "REFERENCE_TEXT_REQUIRED": "参考文字为空，请重新设置声音。",
            "REFERENCE_AUDIO_MISSING": "参考录音不存在，请重新设置声音。",
            "TTS_EMPTY_OUTPUT": "语音模型没有生成有效音频，稍后会自动重试。",
        }
        return messages.get(code, "这一段语音没有生成成功，完整原因已写入本机日志。")

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
