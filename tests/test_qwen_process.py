from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import pytest

from mac_agent.generation import GenerationError
from mac_agent.qwen_process import QwenProcessGenerator


class RecordingInput(io.StringIO):
    def write(self, value: str) -> int:
        result = super().write(value)
        try:
            request = json.loads(value)
        except json.JSONDecodeError:
            return result
        if request.get("command") == "generate":
            output = Path(request["output"])
            with wave.open(str(output), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(24000)
                writer.writeframes(b"\0\0" * 240)
        return result


class FakeProcess:
    def __init__(self, responses: int = 1) -> None:
        self.stdin = RecordingInput()
        response = json.dumps({
            "status": "ok",
            "duration_seconds": 3.25,
            "sample_rate": 24000,
        }) + "\n"
        self.stdout = io.StringIO(response * responses)
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


def test_qwen_generation_resumes_from_completed_small_pieces(tmp_path: Path) -> None:
    output = tmp_path / "segment.wav"
    text = "这是一个用于测试关机续做的小段检查点。" * 5
    first_process = FakeProcess(responses=1)
    first = QwenProcessGenerator(
        python_path=tmp_path / "python",
        worker_script=tmp_path / "worker.py",
        reference_audio=tmp_path / "reference.wav",
        reference_text="参考文字",
        process_factory=lambda _command: first_process,
    )

    with pytest.raises(GenerationError) as captured:
        first.generate(text, output)

    assert captured.value.code == "TTS_WORKER_EXITED"
    first_requests = [
        json.loads(line)
        for line in first_process.stdin.getvalue().splitlines()
        if '"command": "generate"' in line
    ]
    assert len(first_requests) == 2
    assert (output.with_suffix(".parts") / "checkpoint.json").exists()

    second_process = FakeProcess(responses=20)
    progress: list[tuple[int, int, float]] = []
    second = QwenProcessGenerator(
        python_path=tmp_path / "python",
        worker_script=tmp_path / "worker.py",
        reference_audio=tmp_path / "reference.wav",
        reference_text="参考文字",
        process_factory=lambda _command: second_process,
    )
    generated = second.generate(text, output, lambda *value: progress.append(value))
    second_requests = [
        json.loads(line)
        for line in second_process.stdin.getvalue().splitlines()
        if '"command": "generate"' in line
    ]

    assert output.exists()
    assert generated.duration_seconds == pytest.approx(progress[-1][2])
    assert progress[-1][0] == progress[-1][1]
    assert len(second_requests) == progress[-1][1] - 1
