from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

PROTOCOL_STDOUT = sys.stdout
MAX_GENERATION_CHARS = 40


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


def generation_request(request: dict[str, Any]) -> tuple[list[str], list[Path]]:
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


def generation_token_limit(texts: list[str]) -> int:
    """Bound pathological continuations while leaving room for natural pauses."""
    longest = max(len(text) for text in texts)
    return max(60, min(120, (longest * 7 + 1) // 2))


def request_user_initiated_qos() -> None:
    """Keep a launchd child from inheriting background CPU/Metal throttling."""
    if sys.platform != "darwin":
        return
    try:
        qos_user_initiated = 0x19
        libc = ctypes.CDLL(None)
        setter = libc.pthread_set_qos_class_self_np
        setter.argtypes = [ctypes.c_uint, ctypes.c_int]
        setter.restype = ctypes.c_int
        if setter(qos_user_initiated, 0) != 0:
            print("Unable to request user-initiated QoS.", file=sys.stderr)
    except (AttributeError, OSError):
        print("User-initiated QoS is unavailable.", file=sys.stderr)


def error_code(error: BaseException, phase: str) -> str:
    message = str(error).lower()
    if str(error) == "TTS_EMPTY_OUTPUT":
        return "TTS_EMPTY_OUTPUT"
    if isinstance(error, (ModuleNotFoundError, ImportError)):
        return "MLX_DEPENDENCY_MISSING"
    if "out of memory" in message or "metal" in message and "memory" in message:
        return "OUT_OF_MEMORY"
    if phase == "load" and any(
        value in message
        for value in ("not found", "no such file", "offline", "repository not found")
    ):
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


def main() -> None:
    args = parse_args()
    request_user_initiated_qos()
    try:
        # MLX-Audio and model downloads may print progress to stdout. The parent
        # process reserves stdout for one JSON response per input line.
        with contextlib.redirect_stdout(sys.stderr):
            import mlx.core as mx  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
            import soundfile as sf  # type: ignore[import-not-found]
            from mlx_audio.tts.utils import load  # type: ignore[import-not-found]

            model = load(args.model)
    except Exception as error:
        respond(error_response(error, "load"))
        return

    for line in sys.stdin:
        outputs: list[Path] = []
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                break
            if request.get("command") != "generate":
                respond({"status": "error", "code": "BAD_COMMAND"})
                continue
            reference_text = str(request.get("reference_text") or "").strip()
            if not reference_text:
                respond({"status": "error", "code": "REFERENCE_TEXT_REQUIRED"})
                continue
            reference_audio = Path(str(request.get("reference_audio") or ""))
            if not reference_audio.is_file():
                respond({"status": "error", "code": "REFERENCE_AUDIO_MISSING"})
                continue
            texts, outputs = generation_request(request)
            if any(len(text) > MAX_GENERATION_CHARS for text in texts):
                raise ValueError("TTS_TEXT_EXCEEDS_BOUNDED_PIECE")
            for output in outputs:
                output.parent.mkdir(parents=True, exist_ok=True)

            chunks: list[list[Any]] = [[] for _ in texts]
            sample_rates = [0 for _ in texts]
            generation_started = time.perf_counter()
            print(
                f"Generating {len(texts)} piece(s); lengths="
                f"{','.join(str(len(text)) for text in texts)}; "
                f"max_tokens={generation_token_limit(texts)}",
                file=sys.stderr,
            )
            with contextlib.redirect_stdout(sys.stderr):
                results = model.batch_generate(
                    texts=texts,
                    ref_audio=str(reference_audio),
                    ref_text=reference_text,
                    lang_code="Chinese",
                    max_tokens=generation_token_limit(texts),
                    temperature=0.0,
                    top_k=1,
                    top_p=1.0,
                    repetition_penalty=1.1,
                    stream=False,
                    verbose=False,
                )
                for result in results:
                    sequence_index = int(result.sequence_idx)
                    if sequence_index < 0 or sequence_index >= len(texts):
                        raise RuntimeError("TTS_BAD_SEQUENCE_INDEX")
                    chunks[sequence_index].append(np.asarray(result.audio))
                    sample_rates[sequence_index] = int(result.sample_rate)
            print(
                f"Generated batch in {time.perf_counter() - generation_started:.3f}s",
                file=sys.stderr,
            )

            items: list[dict[str, float | int]] = []
            for output, audio_chunks, sample_rate in zip(
                outputs,
                chunks,
                sample_rates,
                strict=True,
            ):
                if not audio_chunks or sample_rate <= 0:
                    raise RuntimeError("TTS_EMPTY_OUTPUT")
                waveform = (
                    audio_chunks[0]
                    if len(audio_chunks) == 1
                    else np.concatenate(audio_chunks)
                )
                if waveform.size <= 0:
                    raise RuntimeError("TTS_EMPTY_OUTPUT")
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

            if len(items) == 1:
                respond({"status": "ok", **items[0]})
            else:
                respond({"status": "ok", "items": items})
        except Exception as error:
            for output in outputs:
                output.unlink(missing_ok=True)
            respond(error_response(error, "generate"))
        finally:
            try:
                mx.clear_cache()
            except Exception:
                pass


if __name__ == "__main__":
    main()
