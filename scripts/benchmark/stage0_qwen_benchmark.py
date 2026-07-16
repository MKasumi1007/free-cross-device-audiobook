#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel


DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a private local Qwen stage-0 benchmark")
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-text", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser.parse_args()


class MemorySampler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.peak_rss = 0
        self.peak_system_used = 0

    def __enter__(self) -> MemorySampler:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        process = psutil.Process()
        while not self._stop.wait(0.1):
            self.peak_rss = max(self.peak_rss, process.memory_info().rss)
            self.peak_system_used = max(self.peak_system_used, psutil.virtual_memory().used)


def device_name() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main() -> None:
    args = parse_args()
    if not args.reference.is_file():
        raise SystemExit(f"Reference does not exist: {args.reference}")
    if not args.reference.resolve().is_relative_to((Path.cwd() / ".local").resolve()):
        raise SystemExit("Stage 0 reference must stay under .local/")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.result.parent.mkdir(parents=True, exist_ok=True)

    device = device_name()
    dtype = torch.bfloat16 if device == "mps" else torch.float32
    started = time.perf_counter()
    with MemorySampler() as sampler:
        load_started = time.perf_counter()
        model = Qwen3TTSModel.from_pretrained(
            args.model,
            device_map=device,
            dtype=dtype,
        )
        load_seconds = time.perf_counter() - load_started

        generation_started = time.perf_counter()
        waveforms, sample_rate = model.generate_voice_clone(
            text=args.text,
            language="Chinese",
            ref_audio=str(args.reference),
            ref_text=args.reference_text,
            x_vector_only_mode=False,
            non_streaming_mode=True,
        )
        generation_seconds = time.perf_counter() - generation_started
        if not waveforms:
            raise RuntimeError("Qwen returned no audio")
        sf.write(args.output, waveforms[0], sample_rate)

    audio_seconds = len(waveforms[0]) / sample_rate
    result: dict[str, Any] = {
        "model": args.model,
        "device": device,
        "dtype": str(dtype),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "load_seconds": round(load_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "audio_seconds": round(audio_seconds, 3),
        "generation_rtf": round(generation_seconds / audio_seconds, 3),
        "total_seconds": round(time.perf_counter() - started, 3),
        "peak_process_rss_bytes": sampler.peak_rss,
        "peak_system_used_bytes": sampler.peak_system_used,
        "sample_rate": sample_rate,
        "output_bytes": os.path.getsize(args.output),
        "output": str(args.output),
    }
    args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

