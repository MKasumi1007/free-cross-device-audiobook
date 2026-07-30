from __future__ import annotations

import re


MAX_GENERATION_CHARS = 40


def split_generation_text(
    text: str,
    max_chars: int = MAX_GENERATION_CHARS,
) -> list[str]:
    """Split model calls at natural boundaries while preserving every character."""
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
