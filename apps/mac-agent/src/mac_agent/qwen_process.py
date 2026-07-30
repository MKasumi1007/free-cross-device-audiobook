from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import wave
from hashlib import sha256
from pathlib import Path
from typing import IO, Any, Callable, Protocol, cast

from .generation import GeneratedAudio, GenerationError
from .tts_text import split_generation_text


class WorkerProcess(Protocol):
    stdin: IO[str] | None
    stdout: IO[str] | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[list[str]], WorkerProcess]
PieceProgressCallback = Callable[[int, int, float], None]


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
        batch_size: int = 1,
        backend_name: str = "qwen",
    ) -> None:
        self.python_path = python_path
        self.worker_script = worker_script
        self.reference_audio = reference_audio
        self.reference_text = reference_text
        self.model = model
        self.idle_seconds = idle_seconds
        self.stderr_path = stderr_path or worker_script.parent / "qwen-stderr.log"
        self.process_factory = process_factory
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.batch_size = batch_size
        self.backend_name = backend_name
        self._process: WorkerProcess | None = None
        self._idle_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def generate(
        self,
        text: str,
        output_wav: Path,
        on_progress: PieceProgressCallback | None = None,
    ) -> GeneratedAudio:
        with self._lock:
            pieces = split_generation_text(text)
            if not pieces:
                raise GenerationError("TTS_EMPTY_OUTPUT", "语音模型没有生成有效音频。")
            parts_root = output_wav.with_suffix(".parts")
            parts_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            fingerprint = self._piece_fingerprint(text)
            completed = self._load_piece_checkpoint(parts_root, fingerprint)
            records: list[dict[str, str | int | float]] = []
            total_duration = 0.0
            sample_rate = 0
            if on_progress:
                on_progress(0, len(pieces), 0.0)
            index = 0
            while index < len(pieces):
                piece = pieces[index]
                part_path = parts_root / f"{index:04d}.wav"
                existing = completed.get(index)
                if (
                    existing
                    and existing.get("text_sha256") == sha256(piece.encode("utf-8")).hexdigest()
                    and part_path.is_file()
                    and part_path.stat().st_size > 44
                ):
                    generated_items = [GeneratedAudio(
                        duration_seconds=float(existing["duration_seconds"]),
                        sample_rate=int(existing["sample_rate"]),
                    )]
                    batch_indices = [index]
                else:
                    batch_indices = []
                    cursor = index
                    while cursor < len(pieces) and len(batch_indices) < self.batch_size:
                        cursor_record = completed.get(cursor)
                        cursor_path = parts_root / f"{cursor:04d}.wav"
                        if (
                            cursor_record
                            and cursor_record.get("text_sha256")
                            == sha256(pieces[cursor].encode("utf-8")).hexdigest()
                            and cursor_path.is_file()
                            and cursor_path.stat().st_size > 44
                        ):
                            break
                        batch_indices.append(cursor)
                        cursor += 1
                    batch_paths = [parts_root / f"{value:04d}.wav" for value in batch_indices]
                    for path in batch_paths:
                        path.unlink(missing_ok=True)
                    generated_items = self._generate_pieces(
                        [pieces[value] for value in batch_indices],
                        batch_paths,
                    )

                for completed_index, generated in zip(
                    batch_indices,
                    generated_items,
                    strict=True,
                ):
                    if sample_rate and generated.sample_rate != sample_rate:
                        raise GenerationError(
                            "AUDIO_VALIDATION_FAILED",
                            "语音模型在同一段中返回了不同采样率。",
                        )
                    sample_rate = generated.sample_rate
                    total_duration += generated.duration_seconds
                    record: dict[str, str | int | float] = {
                        "index": completed_index,
                        "text_sha256": sha256(
                            pieces[completed_index].encode("utf-8")
                        ).hexdigest(),
                        "duration_seconds": generated.duration_seconds,
                        "sample_rate": generated.sample_rate,
                    }
                    records.append(record)
                    self._write_piece_checkpoint(parts_root, fingerprint, records)
                    if on_progress:
                        on_progress(completed_index + 1, len(pieces), total_duration)
                index += len(batch_indices)
            self._concatenate_parts(
                [parts_root / f"{index:04d}.wav" for index in range(len(pieces))],
                output_wav,
            )
            return GeneratedAudio(duration_seconds=total_duration, sample_rate=sample_rate)

    def _generate_pieces(
        self,
        texts: list[str],
        output_wavs: list[Path],
    ) -> list[GeneratedAudio]:
        if not texts or len(texts) != len(output_wavs):
            raise ValueError("texts and output_wavs must be non-empty and aligned")
        process = self._ensure_process()
        if process.stdin is None or process.stdout is None:
            self._stop_locked()
            raise GenerationError("TTS_WORKER_BROKEN", "语音模型进程无法通信。")
        request: dict[str, Any] = {
            "command": "generate",
            "reference_audio": str(self.reference_audio),
            "reference_text": self.reference_text,
        }
        if len(texts) == 1:
            request.update({"text": texts[0], "output": str(output_wavs[0])})
        else:
            request.update({
                "texts": texts,
                "outputs": [str(path) for path in output_wavs],
            })
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            response_line = process.stdout.readline()
            if not response_line:
                exit_code = process.poll()
                self._stop_locked()
                raise GenerationError(
                    "TTS_WORKER_EXITED",
                    "语音模型进程意外退出，稍后会从小批次检查点继续。",
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
                "语音模型意外退出，稍后会从小批次检查点继续。",
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
        raw_items = response.get("items") if len(texts) > 1 else [response]
        try:
            if not isinstance(raw_items, list) or len(raw_items) != len(texts):
                raise ValueError("worker returned the wrong item count")
            generated_items = [
                GeneratedAudio(
                    duration_seconds=float(item["duration_seconds"]),
                    sample_rate=int(item["sample_rate"]),
                )
                for item in raw_items
                if isinstance(item, dict)
            ]
            if len(generated_items) != len(texts):
                raise ValueError("worker returned malformed items")
        except (KeyError, TypeError, ValueError) as error:
            self._stop_locked()
            raise GenerationError(
                "TTS_WORKER_BROKEN",
                "语音模型返回了无法识别的结果，完整响应已写入本机日志。",
                details={"response": response, "stderr_tail": self._stderr_tail()},
            ) from error
        if any(not path.is_file() or path.stat().st_size <= 44 for path in output_wavs):
            raise GenerationError("TTS_EMPTY_OUTPUT", "语音模型没有保存有效的小批次音频。")
        return generated_items

    def discard_checkpoint(self, output_wav: Path) -> None:
        shutil.rmtree(output_wav.with_suffix(".parts"), ignore_errors=True)

    def _piece_fingerprint(self, text: str) -> str:
        try:
            audio_stat = self.reference_audio.stat()
            audio_identity = f"{audio_stat.st_size}:{audio_stat.st_mtime_ns}"
        except OSError:
            audio_identity = str(self.reference_audio)
        return sha256(
            json.dumps(
                {
                    "model": self.model,
                    "backend": self.backend_name,
                    "batch_size": self.batch_size,
                    "reference_audio": audio_identity,
                    "reference_text": self.reference_text,
                    "text": text,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _load_piece_checkpoint(
        parts_root: Path,
        fingerprint: str,
    ) -> dict[int, dict[str, Any]]:
        path = parts_root / "checkpoint.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if payload.get("fingerprint") != fingerprint:
            shutil.rmtree(parts_root, ignore_errors=True)
            parts_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            return {}
        records = payload.get("pieces")
        if not isinstance(records, list):
            return {}
        return {
            int(record["index"]): record
            for record in records
            if isinstance(record, dict) and isinstance(record.get("index"), int)
        }

    @staticmethod
    def _write_piece_checkpoint(
        parts_root: Path,
        fingerprint: str,
        records: list[dict[str, str | int | float]],
    ) -> None:
        path = parts_root / "checkpoint.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"fingerprint": fingerprint, "pieces": records},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        path.chmod(0o600)

    @staticmethod
    def _concatenate_parts(parts: list[Path], destination: Path) -> None:
        temporary = destination.with_suffix(".assembling.wav")
        parameters: tuple[int, int, int] | None = None
        try:
            with wave.open(str(temporary), "wb") as writer:
                for part in parts:
                    with wave.open(str(part), "rb") as reader:
                        current = (
                            reader.getnchannels(),
                            reader.getsampwidth(),
                            reader.getframerate(),
                        )
                        if parameters is None:
                            parameters = current
                            writer.setnchannels(current[0])
                            writer.setsampwidth(current[1])
                            writer.setframerate(current[2])
                        elif current != parameters:
                            raise GenerationError(
                                "AUDIO_VALIDATION_FAILED",
                                "小批次音频格式不一致，稍后会重新生成。",
                            )
                        writer.writeframes(reader.readframes(reader.getnframes()))
            if parameters is None:
                raise GenerationError("TTS_EMPTY_OUTPUT", "没有可合并的小批次音频。")
            os.replace(temporary, destination)
            destination.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def unload(self) -> None:
        with self._lock:
            self._stop_locked()

    def _ensure_process(self) -> WorkerProcess:
        if self.loaded:
            assert self._process is not None
            return self._process
        if not self.python_path.is_file() and self.process_factory is None:
            environment_code = (
                "MLX_ENV_MISSING" if self.backend_name == "mlx" else "QWEN_ENV_MISSING"
            )
            raise GenerationError(
                environment_code,
                "本机声音模型环境不存在，请在系统状态中运行自动修复。",
                details={"python_path": str(self.python_path)},
            )
        if not self.worker_script.is_file() and self.process_factory is None:
            raise GenerationError(
                "TTS_WORKER_MISSING",
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
            "MLX_DEPENDENCY_MISSING": "MLX 依赖不完整，请在系统状态中运行自动修复。",
            "MODEL_NOT_FOUND": "Qwen 模型尚未下载，请在系统状态中运行自动修复。",
            "MODEL_LOAD_FAILED": "声音模型加载失败，完整原因已写入本机日志。",
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
