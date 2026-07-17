from __future__ import annotations

import re
from pathlib import Path

from .errors import BookParseError
from .models import Chapter, SegmentKind
from .normalize import make_chapter, normalize_display_text


CHAPTER_RE = re.compile(
    r"^(?:第[零〇一二三四五六七八九十百千万两\d]+[章节卷回部篇]|chapter\s+\d+\b).{0,80}$",
    re.IGNORECASE,
)


def decode_txt(data: bytes) -> str:
    candidates: list[str]
    if data.startswith(b"\xef\xbb\xbf"):
        candidates = ["utf-8-sig"]
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates = ["utf-16"]
    else:
        candidates = ["utf-8", "gb18030"]

    for encoding in candidates:
        try:
            return data.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    raise BookParseError("BAD_ENCODING", "TXT 编码无法识别，请转换为 UTF-8、UTF-16 或 GB18030。")


def _split_paragraphs(text: str) -> list[str]:
    groups = [normalize_display_text(item) for item in re.split(r"\n\s*\n", text)]
    groups = [item for item in groups if item]
    if len(groups) == 1 and "\n" in groups[0]:
        lines = [normalize_display_text(item) for item in groups[0].splitlines()]
        return [item for item in lines if item]
    return groups


def parse_txt_chapters(data: bytes, source_sha256: str, path: Path) -> tuple[Chapter, ...]:
    text = normalize_display_text(decode_txt(data).replace("\x00", ""))
    if not text:
        raise BookParseError("EMPTY_BOOK", "这本 TXT 没有可朗读的正文。")

    lines = text.splitlines()
    boundaries = [index for index, line in enumerate(lines) if CHAPTER_RE.match(line.strip())]
    sections: list[tuple[str, str]] = []
    if boundaries:
        if any(line.strip() for line in lines[: boundaries[0]]):
            sections.append(("前言", "\n".join(lines[: boundaries[0]])))
        for position, start in enumerate(boundaries):
            end = boundaries[position + 1] if position + 1 < len(boundaries) else len(lines)
            sections.append((lines[start].strip(), "\n".join(lines[start + 1 : end])))
    else:
        sections.append((path.stem, text))

    chapters: list[Chapter] = []
    for order, (title, body) in enumerate(sections):
        paragraphs = _split_paragraphs(body)
        blocks = [(value, value, SegmentKind.PARAGRAPH) for value in paragraphs]
        if not blocks:
            continue
        chapters.append(
            make_chapter(
                source_sha256=source_sha256,
                order=order,
                title=title,
                source_href=f"txt/chapter-{order}",
                blocks=blocks,
            )
        )

    if not chapters:
        raise BookParseError("EMPTY_BOOK", "这本 TXT 没有可朗读的正文。")
    return tuple(chapters)
