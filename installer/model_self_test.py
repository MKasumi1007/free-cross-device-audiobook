#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
REFERENCE_TEXT = "今天使用合成声音检查听书工具。这个检查不会使用用户的真实声音。"
OUTPUT_TEXT = "窗外有风，书页轻轻翻动。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--download-only", action="store_true")
    return parser.parse_args()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {command[0]}\n{result.stderr[-6000:]}"
        )
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def duration_from_progress(result: subprocess.CompletedProcess[str]) -> float:
    values = dict(
        line.partition("=")[::2]
        for line in result.stdout.splitlines()
        if "=" in line
    )
    raw_microseconds = values.get("out_time_us") or values.get("out_time_ms")
    if not raw_microseconds:
        raise RuntimeError("FFmpeg did not report an audio duration")
    duration = int(raw_microseconds) / 1_000_000
    if duration <= 0:
        raise RuntimeError("encoded M4A has no positive duration")
    return duration


def write_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main() -> None:
    args = parse_args()
    os.environ["HF_HOME"] = str(args.data_root / "models/huggingface")
    state_path = args.data_root / "state/model-self-test.json"
    started = time.perf_counter()
    try:
        import soundfile as sf
        import torch
        from huggingface_hub import snapshot_download
        from qwen_tts import Qwen3TTSModel

        model_path = Path(snapshot_download(
            repo_id=MODEL,
            cache_dir=args.data_root / "models/huggingface/hub",
        ))
        if args.download_only:
            write_state(state_path, {
                "status": "downloaded",
                "checked_at": datetime.now(UTC).isoformat(),
                "model": MODEL,
                "model_path": str(model_path),
            })
            return

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        dtype = torch.bfloat16 if device == "mps" else torch.float32
        with tempfile.TemporaryDirectory(prefix="model-self-test-", dir=args.data_root / "state") as raw:
            work = Path(raw)
            reference_aiff = work / "reference.aiff"
            reference_wav = work / "reference.wav"
            output_wav = work / "output.wav"
            output_m4a = work / "output.m4a"
            run(["/usr/bin/say", "-v", "Tingting", "-r", "155", "-o", str(reference_aiff), REFERENCE_TEXT])
            run([
                str(args.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(reference_aiff), "-ar", "24000", "-ac", "1", str(reference_wav),
            ])
            load_started = time.perf_counter()
            model = Qwen3TTSModel.from_pretrained(str(model_path), device_map=device, dtype=dtype)
            load_seconds = time.perf_counter() - load_started
            generation_started = time.perf_counter()
            waveforms, sample_rate = model.generate_voice_clone(
                text=OUTPUT_TEXT,
                language="Chinese",
                ref_audio=str(reference_wav),
                ref_text=REFERENCE_TEXT,
                x_vector_only_mode=False,
                non_streaming_mode=True,
            )
            generation_seconds = time.perf_counter() - generation_started
            if not waveforms:
                raise RuntimeError("Qwen returned no waveform")
            sf.write(output_wav, waveforms[0], sample_rate, format="WAV")
            wav_info = sf.info(output_wav)
            if wav_info.frames <= 0 or wav_info.samplerate != sample_rate:
                raise RuntimeError("generated WAV is invalid")
            run([
                str(args.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(output_wav), "-c:a", "aac", "-b:a", "64k",
                "-movflags", "+faststart", str(output_m4a),
            ])
            probe = run([
                str(args.ffmpeg), "-hide_banner", "-loglevel", "error",
                "-i", str(output_m4a), "-map", "0:a:0", "-f", "null", "-",
                "-progress", "pipe:1", "-nostats",
            ])
            duration = duration_from_progress(probe)
            if duration <= 0 or output_m4a.stat().st_size <= 0:
                raise RuntimeError("encoded M4A is invalid")
            write_state(state_path, {
                "status": "ok",
                "checked_at": datetime.now(UTC).isoformat(),
                "model": MODEL,
                "model_path": str(model_path),
                "device": device,
                "dtype": str(dtype),
                "python": platform.python_version(),
                "torch": torch.__version__,
                "load_seconds": round(load_seconds, 3),
                "generation_seconds": round(generation_seconds, 3),
                "audio_seconds": round(duration, 3),
                "sample_rate": sample_rate,
                "wav_sha256": sha256(output_wav),
                "m4a_sha256": sha256(output_m4a),
                "m4a_bytes": output_m4a.stat().st_size,
                "total_seconds": round(time.perf_counter() - started, 3),
            })
    except Exception as error:
        write_state(state_path, {
            "status": "failed",
            "checked_at": datetime.now(UTC).isoformat(),
            "model": MODEL,
            "error_type": type(error).__name__,
            "error": str(error),
        })
        raise


if __name__ == "__main__":
    main()
