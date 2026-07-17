from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class PublicationMode(StrEnum):
    LOCAL_ONLY = "LOCAL_ONLY"
    PUBLIC_RIGHTS_CONFIRMED = "PUBLIC_RIGHTS_CONFIRMED"


class SegmentKind(StrEnum):
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    FOOTNOTE = "FOOTNOTE"


@dataclass(frozen=True)
class TextSegment:
    segment_id: str
    chapter_id: str
    order: int
    display_text: str
    spoken_text: str
    text_hash: str
    kind: SegmentKind = SegmentKind.PARAGRAPH


@dataclass(frozen=True)
class Chapter:
    chapter_id: str
    order: int
    title: str
    source_href: str
    segments: tuple[TextSegment, ...]


@dataclass(frozen=True)
class ParsedBook:
    book_id: str
    title: str
    author: str
    source_format: str
    source_sha256: str
    publication_mode: PublicationMode
    chapters: tuple[Chapter, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    rights_confirmed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def segment_count(self) -> int:
        return sum(len(chapter.segments) for chapter in self.chapters)


@dataclass(frozen=True)
class ParserLimits:
    max_file_bytes: int = 200 * 1024 * 1024
    max_zip_entries: int = 20_000
    max_total_uncompressed_bytes: int = 1024 * 1024 * 1024
    max_entry_uncompressed_bytes: int = 200 * 1024 * 1024
    max_compression_ratio: float = 100.0


def assert_publication_allowed(book: ParsedBook) -> None:
    if book.publication_mode is not PublicationMode.PUBLIC_RIGHTS_CONFIRMED:
        from .errors import BookParseError

        raise BookParseError(
            "RIGHTS_NOT_CONFIRMED",
            "尚未确认这本书的传播权，不能创建公开上传任务。",
        )
