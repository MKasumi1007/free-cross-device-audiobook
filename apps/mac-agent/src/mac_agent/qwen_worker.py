from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import soundfile as sf  # type: ignore[import-not-found]
import torch  # type: ignore[import-not-found]
from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    return parser.parse_args()


def respond(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


def main() -> None:
    args = parse_args()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "mps" else torch.float32
    model = Qwen3TTSModel.from_pretrained(args.model, device_map=device, dtype=dtype)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                break
            if request.get("command") != "generate":
                respond({"status": "error", "code": "BAD_COMMAND"})
                continue
            output = Path(request["output"])
            output.parent.mkdir(parents=True, exist_ok=True)
            waveforms, sample_rate = model.generate_voice_clone(
                text=request["text"],
                language="Chinese",
                ref_audio=request["reference_audio"],
                ref_text=request["reference_text"],
                x_vector_only_mode=False,
                non_streaming_mode=True,
            )
            if not waveforms:
                respond({"status": "error", "code": "TTS_EMPTY_OUTPUT"})
                continue
            sf.write(output, waveforms[0], sample_rate, format="WAV")
            respond({
                "status": "ok",
                "duration_seconds": len(waveforms[0]) / sample_rate,
                "sample_rate": sample_rate,
            })
        except Exception:
            respond({"status": "error", "code": "TTS_GENERATION_FAILED"})


if __name__ == "__main__":
    main()
