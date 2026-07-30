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
MAX_GENERATION_CHARS = 40


def split_generation_text(text: str, max_chars: int = MAX_GENERATION_CHARS) -> list[str]:
    """Keep this script standalone because it runs inside the separate Qwen environment."""
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


def generation_request(request: dict[str, Any]) -> tuple[list[str], list[Path]]:
    """Accept the legacy single-item protocol and the bounded batch protocol."""
    if "texts" in request or "outputs" in request:
        texts = request.get("texts")
        outputs = request.get("outputs")
        if (
            not isinstance(texts, list)
            or not isinstance(outputs, list)
            or not texts
            or len(texts) != len(outputs)
            or any(not isinstance(value, str) or not value.strip() for value in texts)
            or any(not isinstance(value, str) or not value for value in outputs)
        ):
            raise ValueError("TTS_BAD_BATCH")
        return [value.strip() for value in texts], [Path(value) for value in outputs]
    text = str(request.get("text") or "").strip()
    output = str(request.get("output") or "")
    if not text or not output:
        raise ValueError("TTS_BAD_REQUEST")
    return [text], [Path(output)]


def reference_identity(path: Path, transcript: str) -> tuple[str, int, int, str]:
    stat = path.stat()
    return (str(path), stat.st_size, stat.st_mtime_ns, transcript)


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
    clone_prompt_key: tuple[str, int, int, str] | None = None
    clone_prompt: Any = None
    for line in sys.stdin:
        outputs: list[Path] = []
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
            texts, outputs = generation_request(request)
            if any(len(split_generation_text(text)) != 1 for text in texts):
                raise ValueError("TTS_TEXT_EXCEEDS_BOUNDED_PIECE")
            for output in outputs:
                output.parent.mkdir(parents=True, exist_ok=True)

            current_prompt_key = reference_identity(
                reference_audio,
                request["reference_text"],
            )
            if clone_prompt_key != current_prompt_key:
                with contextlib.redirect_stdout(sys.stderr):
                    clone_prompt = model.create_voice_clone_prompt(
                        ref_audio=str(reference_audio),
                        ref_text=request["reference_text"],
                        x_vector_only_mode=False,
                    )
                clone_prompt_key = current_prompt_key

            waveforms: Any = None
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    waveforms, piece_rate = model.generate_voice_clone(
                        text=texts[0] if len(texts) == 1 else texts,
                        language="Chinese" if len(texts) == 1 else ["Chinese"] * len(texts),
                        voice_clone_prompt=clone_prompt,
                        non_streaming_mode=True,
                    )
                if not waveforms or len(waveforms) != len(outputs):
                    raise RuntimeError("TTS_EMPTY_OUTPUT")
                sample_rate = int(piece_rate)
                items: list[dict[str, float | int]] = []
                for output, waveform in zip(outputs, waveforms, strict=True):
                    sf.write(
                        output,
                        waveform,
                        sample_rate,
                        format="WAV",
                        subtype="PCM_16",
                    )
                    info = sf.info(output)
                    if info.frames <= 0 or info.samplerate != sample_rate:
                        raise RuntimeError("TTS_EMPTY_OUTPUT")
                    items.append({
                        "duration_seconds": info.frames / info.samplerate,
                        "sample_rate": sample_rate,
                    })
            finally:
                waveforms = None
                release_accelerator_cache(torch)

            if len(items) == 1:
                respond({"status": "ok", **items[0]})
            else:
                respond({"status": "ok", "items": items})
        except Exception as error:
            for output in outputs:
                output.unlink(missing_ok=True)
            respond(error_response(error, "generate"))


if __name__ == "__main__":
    main()
