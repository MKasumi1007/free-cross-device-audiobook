from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import mac_agent.qwen_worker
from mac_agent.qwen_worker import release_accelerator_cache
from mac_agent.tts_text import split_generation_text


def test_generation_text_is_split_at_natural_bounded_breaks() -> None:
    pieces = split_generation_text("第一句很短。" + "这是一段较长文字，" * 20, max_chars=40)
    assert "".join(pieces) == "第一句很短。" + "这是一段较长文字，" * 20
    assert len(pieces) > 1
    assert all(0 < len(piece) <= 40 for piece in pieces)


def test_default_generation_piece_is_small_enough_for_an_8gb_mac() -> None:
    pieces = split_generation_text("这是用于限制统一内存峰值的一段较长测试文字。" * 8)

    assert len(pieces) > 1
    assert all(0 < len(piece) <= 40 for piece in pieces)


def test_accelerator_cache_is_released_when_mps_is_available() -> None:
    calls: list[str] = []

    class FakeMps:
        @staticmethod
        def empty_cache() -> None:
            calls.append("released")

    class FakeTorch:
        mps = FakeMps()

    release_accelerator_cache(FakeTorch())

    assert calls == ["released"]


def test_third_party_stdout_cannot_corrupt_worker_protocol(tmp_path: Path) -> None:
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    (fakes / "torch.py").write_text(
        "class _Mps:\n"
        " @staticmethod\n"
        " def is_available(): return False\n"
        "class _Backends: mps = _Mps()\n"
        "backends = _Backends()\n"
        "float32 = 'float32'\n"
        "bfloat16 = 'bfloat16'\n",
        encoding="utf-8",
    )
    (fakes / "qwen_tts.py").write_text(
        "print('library banner on stdout')\n"
        "class _Model:\n"
        " def generate_voice_clone(self, **kwargs):\n"
        "  print('generation progress on stdout')\n"
        "  return [[0.0, 0.0, 0.0]], 24000\n"
        "class Qwen3TTSModel:\n"
        " @staticmethod\n"
        " def from_pretrained(*args, **kwargs): return _Model()\n",
        encoding="utf-8",
    )
    (fakes / "soundfile.py").write_text(
        "from pathlib import Path\n"
        "class SoundFile:\n"
        " def __init__(self, path, **kwargs): self.path = Path(path); self.path.write_bytes(b'')\n"
        " def write(self, data): self.path.write_bytes(self.path.read_bytes() + b'WAV')\n"
        " def close(self): pass\n"
        "class _Info:\n"
        " frames = 3\n"
        " samplerate = 24000\n"
        "def info(path): return _Info()\n",
        encoding="utf-8",
    )
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"WAV")
    output = tmp_path / "output.wav"
    request = {
        "command": "generate",
        "reference_audio": str(reference),
        "reference_text": "参考文字",
        "text": "测试文字",
        "output": str(output),
    }
    environment = {**os.environ, "PYTHONPATH": str(fakes)}

    result = subprocess.run(
        [sys.executable, str(Path(mac_agent.qwen_worker.__file__)), "--model", "fake"],
        input=json.dumps(request, ensure_ascii=False) + "\n",
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "status": "ok",
        "duration_seconds": 3 / 24000,
        "sample_rate": 24000,
    }
    assert "library banner" in result.stderr
    assert "generation progress" in result.stderr
    assert output.read_bytes() == b"WAV"
