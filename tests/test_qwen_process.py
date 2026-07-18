from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mac_agent.generation import GenerationError
from mac_agent.qwen_process import QwenProcessGenerator


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(json.dumps({
            "status": "ok",
            "duration_seconds": 3.25,
            "sample_rate": 24000,
        }) + "\n")
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


def test_qwen_subprocess_protocol_keeps_model_inputs_out_of_command_line(tmp_path: Path) -> None:
    process = FakeProcess()
    commands: list[list[str]] = []

    def start(command: list[str]) -> FakeProcess:
        commands.append(command)
        return process

    generator = QwenProcessGenerator(
        python_path=tmp_path / "private-env/python",
        worker_script=tmp_path / "qwen_worker.py",
        reference_audio=tmp_path / "private voice.wav",
        reference_text="私密参考文字",
        idle_seconds=3600,
        process_factory=start,
    )
    result = generator.generate("私密书籍正文", tmp_path / "segment.wav")
    assert result.duration_seconds == 3.25
    assert result.sample_rate == 24000
    command_text = " ".join(commands[0])
    assert "私密参考文字" not in command_text
    assert "私密书籍正文" not in command_text
    request = process.stdin.getvalue()
    assert "私密参考文字" in request
    assert "私密书籍正文" in request
    generator.unload()
    assert process.returncode == 0


def test_qwen_worker_error_preserves_private_details_for_local_log(tmp_path: Path) -> None:
    process = FakeProcess()
    process.stdout = io.StringIO(json.dumps({
        "status": "error",
        "code": "MODEL_LOAD_FAILED",
        "error_type": "RuntimeError",
        "error": "invalid model shard",
        "traceback": "private traceback",
    }) + "\n")
    generator = QwenProcessGenerator(
        python_path=tmp_path / "python",
        worker_script=tmp_path / "worker.py",
        reference_audio=tmp_path / "reference.wav",
        reference_text="private reference",
        process_factory=lambda _command: process,
    )

    with pytest.raises(GenerationError) as captured:
        generator.generate("private text", tmp_path / "output.wav")

    assert captured.value.code == "MODEL_LOAD_FAILED"
    assert captured.value.details["worker_error"] == "invalid model shard"
    assert captured.value.details["worker_traceback"] == "private traceback"
