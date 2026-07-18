from __future__ import annotations

import json
import os
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Any

from audiobook_core.models import ParsedBook
from audiobook_core.normalize import concise_chapter_title, is_placeholder_chapter_title
from audiobook_core.parser import parse_book

from .picker import BookPicker


class LocalLibrary:
    def __init__(self, root: Path, picker: BookPicker) -> None:
        self.root = root
        self.picker = picker

    def choose_and_import(self, *, import_as_copy: bool, rights_confirmed: bool) -> ParsedBook | None:
        selected = self.picker.choose()
        if selected is None:
            return None
        duplicate_salt = secrets.token_urlsafe(12) if import_as_copy else ""
        book = parse_book(
            selected,
            rights_confirmed=rights_confirmed,
            duplicate_salt=duplicate_salt,
        )
        existing = self._find_by_source_hash(book.source_sha256)
        if existing and not import_as_copy:
            return self._load(existing)
        self._save(book)
        return book

    def get(self, book_id: str) -> ParsedBook | None:
        path = self.root / book_id / "book.json"
        return self._load(path) if path.is_file() else None

    def _find_by_source_hash(self, source_sha256: str) -> Path | None:
        if not self.root.exists():
            return None
        for book_file in self.root.glob("*/book.json"):
            try:
                payload = json.loads(book_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("source_sha256") == source_sha256:
                return book_file
        return None

    @staticmethod
    def _load(path: Path) -> ParsedBook:
        from audiobook_core.models import Chapter, PublicationMode, SegmentKind, TextSegment

        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        chapters = []
        for raw_chapter in payload["chapters"]:
            segments = tuple(
                TextSegment(
                    **{
                        **segment,
                        "kind": SegmentKind(segment["kind"]),
                    }
                )
                for segment in raw_chapter["segments"]
            )
            chapters.append(Chapter(**{**raw_chapter, "segments": segments}))
        book = ParsedBook(
            **{
                **payload,
                "publication_mode": PublicationMode(payload["publication_mode"]),
                "chapters": tuple(chapters),
                "warnings": tuple(payload.get("warnings", [])),
            }
        )
        repaired = []
        for chapter in book.chapters:
            if not is_placeholder_chapter_title(chapter.title):
                repaired.append(chapter)
                continue
            readable = next(
                (
                    segment.display_text
                    for segment in chapter.segments
                    if segment.kind is not SegmentKind.FOOTNOTE
                    and not is_placeholder_chapter_title(segment.display_text)
                ),
                "",
            )
            title = concise_chapter_title(readable)
            repaired.append(replace(chapter, title=title) if title else chapter)
        return replace(book, chapters=tuple(repaired))

    def _save(self, book: ParsedBook) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        target_dir = self.root / book.book_id
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target_dir.chmod(0o700)
        target = target_dir / "book.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(book.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, target)
