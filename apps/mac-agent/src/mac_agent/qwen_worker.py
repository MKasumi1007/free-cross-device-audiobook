from __future__ import annotations

import argparse
import contextlib
import gc
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any


PROTOCOL_STDOUT = sys.stdout
MAX_GENERATION_CHARS = 120


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    return parser.parse_args()


def respond(value: dict[str, Any]) -> None:
    print(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        file=PROTOCOL_STDOUT,
        flush=True,
    )


def error_code(error: BaseException, phase: str) -> str:
    message = str(error).lower()
    if str(error) == "TTS_EMPTY_OUTPUT":
        return "TTS_EMPTY_OUTPUT"
    if str(error) == "TTS_SAMPLE_RATE_CHANGED":
        return "AUDIO_VALIDATION_FAILED"
    if isinstance(error, (ModuleNotFoundError, ImportError)):
        return "QWEN_DEPENDENCY_MISSING"
    if "out of memory" in message or "mps backend out of memory" in message:
        return "OUT_OF_MEMORY"
    if phase == "load" and any(value in message for value in ("not found", "no such file", "offline")):
        return "MODEL_NOT_FOUND"
    return "MODEL_LOAD_FAILED" if phase == "load" else "TTS_GENERATION_FAILED"


def error_response(error: BaseException, phase: str) -> dict[str, Any]:
    return {
        "status": "error",
        "code": error_code(error, phase),
        "phase": phase,
        "error_type": type(error).__name__,
        "error": str(error),
        "traceback": "".join(traceback.format_exception(error))[-16_000:],
    }


def split_generation_text(text: str, max_chars: int = MAX_GENERATION_CHARS) -> list[str]:
    """Bound one model call while preserving the original segment as one WAV."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    sentences = re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", text.strip())
    bounded: list[str] = []
    for sentence in sentences:
        remaining = sentence.strip()
        while len(remaining) > max_chars:
            window = remaining[:max_chars]
            punctuation = max(window.rfind("，"), window.rfind(","), window.rfind("、"))
            cut = punctuation + 1 if punctuation >= max_chars // 2 else max_chars
            bounded.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            bounded.append(remaining)

    combined: list[str] = []
    for piece in bounded:
        if combined and len(combined[-1]) + len(piece) <= max_chars:
            combined[-1] += piece
        else:
            combined.append(piece)
    return combined


def release_accelerator_cache(torch_module: Any) -> None:
    """Release per-call MPS allocations without failing the generation worker."""
    gc.collect()
    try:
        empty_cache = getattr(getattr(torch_module, "mps", None), "empty_cache", None)
        if callable(empty_cache):
            empty_cache()
    except RuntimeError:
        # Cache cleanup is best-effort; the generated audio is still valid.
        pass


def main() -> None:
    args = parse_args()
    try:
        # qwen-tts and its dependencies print banners/progress to stdout. The
        # parent process uses stdout as a JSON-lines protocol, so all third-
        # party output must go to the private stderr log instead.
        with contextlib.redirect_stdout(sys.stderr):
            import soundfile as sf  # type: ignore[import-not-found]
            import torch  # type: ignore[import-not-found]
            from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]

            mps_available = torch.backends.mps.is_available()
            if not mps_available and os.environ.get("AUDIOBOOK_REQUIRE_MPS") == "1":
                raise RuntimeError("MPS_UNAVAILABLE")
            device = "mps" if mps_available else "cpu"
            dtype = torch.bfloat16 if device == "mps" else torch.float32
            model = Qwen3TTSModel.from_pretrained(args.model, device_map=device, dtype=dtype)
    except Exception as error:
        response = error_response(error, "load")
        if str(error) == "MPS_UNAVAILABLE":
            response["code"] = "MPS_UNAVAILABLE"
        respond(response)
        return
    for line in sys.stdin:
        output: Path | None = None
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                break
            if request.get("command") != "generate":
                respond({"status": "error", "code": "BAD_COMMAND"})
                continue
            if not str(request.get("reference_text") or "").strip():
                respond({"status": "error", "code": "REFERENCE_TEXT_REQUIRED"})
                continue
            reference_audio = Path(str(request.get("reference_audio") or ""))
            if not reference_audio.is_file():
                respond({"status": "error", "code": "REFERENCE_AUDIO_MISSING"})
                continue
            output = Path(request["output"])
            output.parent.mkdir(parents=True, exist_ok=True)
            pieces = split_generation_text(str(request["text"]))
            if not pieces:
                respond({"status": "error", "code": "TTS_EMPTY_OUTPUT"})
                continue
            writer: Any = None
            sample_rate = 0
            try:
                for piece in pieces:
                    waveforms: Any = None
                    try:
                        with contextlib.redirect_stdout(sys.stderr):
                            waveforms, piece_rate = model.generate_voice_clone(
                                text=piece,
                                language="Chinese",
                                ref_audio=str(reference_audio),
                                ref_text=request["reference_text"],
                                x_vector_only_mode=False,
                                non_streaming_mode=True,
                            )
                        if not waveforms:
                            raise RuntimeError("TTS_EMPTY_OUTPUT")
                        if writer is None:
                            sample_rate = int(piece_rate)
                            writer = sf.SoundFile(
                                output,
                                mode="w",
                                samplerate=sample_rate,
                                channels=1,
                                format="WAV",
                                subtype="PCM_16",
                            )
                        elif int(piece_rate) != sample_rate:
                            raise RuntimeError("TTS_SAMPLE_RATE_CHANGED")
                        writer.write(waveforms[0])
                    finally:
                        waveforms = None
                        release_accelerator_cache(torch)
            finally:
                if writer is not None:
                    writer.close()
            info = sf.info(output)
            if info.frames <= 0 or info.samplerate <= 0:
                output.unlink(missing_ok=True)
                respond({"status": "error", "code": "TTS_EMPTY_OUTPUT"})
                continue
            respond({
                "status": "ok",
                "duration_seconds": info.frames / info.samplerate,
                "sample_rate": sample_rate,
            })
        except Exception as error:
            if output is not None:
                output.unlink(missing_ok=True)
            respond(error_response(error, "generate"))


if __name__ == "__main__":
    main()
