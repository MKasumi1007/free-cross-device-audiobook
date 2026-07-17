from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .epub import parse_epub_chapters
from .errors import BookParseError
from .models import ParsedBook, ParserLimits, PublicationMode
from .txt import parse_txt_chapters


def _hash_file(path: Path, limit: int) -> str:
    size = path.stat().st_size
    if size > limit:
        raise BookParseError("FILE_TOO_LARGE", "文件超过 200 MiB，已停止导入。")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_book(
    path: Path,
    *,
    rights_confirmed: bool = False,
    limits: ParserLimits | None = None,
    duplicate_salt: str = "",
) -> ParsedBook:
    limits = limits or ParserLimits()
    path = path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise BookParseError("NOT_A_FILE", "请选择一个 EPUB 或 TXT 文件。")
    suffix = path.suffix.lower()
    if suffix not in {".epub", ".txt"}:
        raise BookParseError("UNSUPPORTED_FORMAT", "目前只支持无 DRM 的 EPUB 和 TXT。")

    source_sha256 = _hash_file(path, limits.max_file_bytes)
    publication_mode = (
        PublicationMode.PUBLIC_RIGHTS_CONFIRMED if rights_confirmed else PublicationMode.LOCAL_ONLY
    )
    rights_confirmed_at = datetime.now(UTC).isoformat() if rights_confirmed else None
    book_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"audiobook:{source_sha256}:{duplicate_salt}"))

    if suffix == ".epub":
        title, author, chapters, warnings = parse_epub_chapters(path, source_sha256, limits)
    else:
        chapters = parse_txt_chapters(path.read_bytes(), source_sha256, path)
        title, author, warnings = path.stem, "", ()

    return ParsedBook(
        book_id=book_id,
        title=title,
        author=author,
        source_format=suffix.removeprefix(".").upper(),
        source_sha256=source_sha256,
        publication_mode=publication_mode,
        chapters=chapters,
        warnings=warnings,
        rights_confirmed_at=rights_confirmed_at,
    )
