from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid

from .models import Chapter, SegmentKind, TextSegment


SPACE_RE = re.compile(r"[\t\f\v ]+")
BLANK_RE = re.compile(r"\n{3,}")


def normalize_display_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [SPACE_RE.sub(" ", line).strip() for line in value.split("\n")]
    return BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


def normalize_spoken_text(value: str) -> str:
    value = normalize_display_text(value)
    return re.sub(r"\s+", " ", value).strip()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_namespace(source_sha256: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"audiobook-source:{source_sha256}")


def make_chapter(
    *,
    source_sha256: str,
    order: int,
    title: str,
    source_href: str,
    blocks: list[tuple[str, str, SegmentKind]],
) -> Chapter:
    namespace = source_namespace(source_sha256)
    normalized_href = source_href.replace("\\", "/").strip()
    chapter_id = str(uuid.uuid5(namespace, f"chapter:{order}:{normalized_href}"))
    segments: list[TextSegment] = []

    for paragraph_order, (display, spoken, kind) in enumerate(blocks):
        display = normalize_display_text(display)
        spoken = normalize_spoken_text(spoken)
        if not display:
            continue
        text_hash = sha256_text(f"{display}\0{spoken}\0{kind.value}")
        segment_id = str(
            uuid.uuid5(
                namespace,
                f"segment:{normalized_href}:{order}:{paragraph_order}:{text_hash}",
            )
        )
        segments.append(
            TextSegment(
                segment_id=segment_id,
                chapter_id=chapter_id,
                order=len(segments),
                display_text=display,
                spoken_text=spoken,
                text_hash=text_hash,
                kind=kind,
            )
        )

    return Chapter(
        chapter_id=chapter_id,
        order=order,
        title=normalize_display_text(title) or f"第 {order + 1} 节",
        source_href=normalized_href,
        segments=tuple(segments),
    )
