from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import mac_agent.mlx_worker
from mac_agent.mlx_worker import generation_token_limit


def test_mlx_generation_token_limit_bounds_short_and_long_pieces() -> None:
    assert generation_token_limit(["短句"]) == 60
    assert generation_token_limit(["这是一个长度适中的中文句子。" * 2]) <= 120
    assert generation_token_limit(["字" * 40]) == 120


def test_mlx_worker_batches_two_pieces_and_keeps_stdout_as_json(tmp_path: Path) -> None:
    fakes = tmp_path / "fakes"
    (fakes / "mlx").mkdir(parents=True)
    (fakes / "mlx_audio/tts").mkdir(parents=True)
    (fakes / "mlx/__init__.py").write_text("", encoding="utf-8")
    (fakes / "mlx/core.py").write_text(
        "def clear_cache(): pass\n",
        encoding="utf-8",
    )
    (fakes / "numpy.py").write_text(
        "class Array(list):\n"
        " @property\n"
        " def size(self): return len(self)\n"
        "def asarray(value): return Array(value)\n"
        "def concatenate(values): return Array(item for value in values for item in value)\n",
        encoding="utf-8",
    )
    (fakes / "soundfile.py").write_text(
        "from pathlib import Path\n"
        "def write(path, data, samplerate, **kwargs): Path(path).write_bytes(b'MLXWAV')\n"
        "class _Info:\n"
        " frames = 4\n"
        " samplerate = 24000\n"
        "def info(path): return _Info()\n",
        encoding="utf-8",
    )
    (fakes / "mlx_audio/__init__.py").write_text("", encoding="utf-8")
    (fakes / "mlx_audio/tts/__init__.py").write_text("", encoding="utf-8")
    (fakes / "mlx_audio/tts/utils.py").write_text(
        "print('mlx library banner')\n"
        "class _Result:\n"
        " def __init__(self, index):\n"
        "  self.sequence_idx = index\n"
        "  self.audio = [0.0, 0.0, 0.0, 0.0]\n"
        "  self.sample_rate = 24000\n"
        "class _Model:\n"
        " def batch_generate(self, **kwargs):\n"
        "  print('batch progress')\n"
        "  assert kwargs['ref_text'] == '参考文字'\n"
        "  assert kwargs['lang_code'] == 'Chinese'\n"
        "  assert kwargs['max_tokens'] == 60\n"
        "  assert kwargs['temperature'] == 0.0\n"
        "  assert kwargs['top_k'] == 1\n"
        "  return [_Result(index) for index, _ in enumerate(kwargs['texts'])]\n"
        "def load(model):\n"
        " print('loading ' + model)\n"
        " return _Model()\n",
        encoding="utf-8",
    )
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"WAV")
    outputs = [tmp_path / "one.wav", tmp_path / "two.wav"]
    request = {
        "command": "generate",
        "reference_audio": str(reference),
        "reference_text": "参考文字",
        "texts": ["第一小段", "第二小段"],
        "outputs": [str(path) for path in outputs],
    }

    result = subprocess.run(
        [sys.executable, str(Path(mac_agent.mlx_worker.__file__)), "--model", "fake"],
        input=json.dumps(request, ensure_ascii=False) + "\n",
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(fakes)},
    )

    assert result.returncode == 0
    response = json.loads(result.stdout)
    assert response["status"] == "ok"
    assert len(response["items"]) == 2
    assert "mlx library banner" in result.stderr
    assert "batch progress" in result.stderr
    assert all(path.read_bytes() == b"MLXWAV" for path in outputs)


def test_mlx_worker_reports_a_missing_reference_without_loading_audio(tmp_path: Path) -> None:
    fakes = tmp_path / "fakes"
    (fakes / "mlx").mkdir(parents=True)
    (fakes / "mlx_audio/tts").mkdir(parents=True)
    (fakes / "mlx/__init__.py").write_text("", encoding="utf-8")
    (fakes / "mlx/core.py").write_text("def clear_cache(): pass\n", encoding="utf-8")
    (fakes / "numpy.py").write_text("", encoding="utf-8")
    (fakes / "soundfile.py").write_text("", encoding="utf-8")
    (fakes / "mlx_audio/__init__.py").write_text("", encoding="utf-8")
    (fakes / "mlx_audio/tts/__init__.py").write_text("", encoding="utf-8")
    (fakes / "mlx_audio/tts/utils.py").write_text(
        "class _Model: pass\n"
        "def load(model): return _Model()\n",
        encoding="utf-8",
    )
    request = {
        "command": "generate",
        "reference_audio": str(tmp_path / "missing.wav"),
        "reference_text": "参考文字",
        "text": "测试",
        "output": str(tmp_path / "output.wav"),
    }

    result = subprocess.run(
        [sys.executable, str(Path(mac_agent.mlx_worker.__file__)), "--model", "fake"],
        input=json.dumps(request, ensure_ascii=False) + "\n",
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(fakes)},
    )

    assert json.loads(result.stdout)["code"] == "REFERENCE_AUDIO_MISSING"
