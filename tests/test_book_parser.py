from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_core.errors import BookParseError
from audiobook_core.models import ParserLimits, SegmentKind
from audiobook_core.parser import parse_book
from tests.fixtures.builders import (
    make_drm_epub,
    make_empty_epub,
    make_epub2_ncx,
    make_epub3_nav,
    make_epub_without_toc,
    make_high_ratio_epub,
    make_traversal_epub,
)


def test_epub3_nested_nav_ruby_and_footnote(tmp_path: Path) -> None:
    path = make_epub3_nav(tmp_path / "nav.epub")
    book = parse_book(path)

    assert book.title == "三径书屋"
    assert [chapter.title for chapter in book.chapters] == ["卷一 入园", "卷二 煮茶"]
    spoken = "".join(segment.spoken_text for chapter in book.chapters for segment in chapter.segments)
    assert "山色" in spoken
    assert "shān" not in spoken
    footnote = next(segment for chapter in book.chapters for segment in chapter.segments if segment.kind is SegmentKind.FOOTNOTE)
    assert footnote.display_text
    assert footnote.spoken_text == ""


def test_epub2_ncx_and_linear_no(tmp_path: Path) -> None:
    book = parse_book(make_epub2_ncx(tmp_path / "ncx.epub"))
    assert [chapter.title for chapter in book.chapters] == ["上篇 风来", "下篇 月明"]
    assert all("cover" not in chapter.source_href for chapter in book.chapters)


def test_epub_without_toc_falls_back_to_spine(tmp_path: Path) -> None:
    book = parse_book(make_epub_without_toc(tmp_path / "fallback.epub"))
    assert [chapter.title for chapter in book.chapters] == ["第一札", "第二札"]
    assert any("正文顺序" in warning for warning in book.warnings)


@pytest.mark.parametrize(
    ("encoding", "suffix"),
    [("utf-16", "utf16"), ("gb18030", "gb")],
)
def test_txt_supported_encodings_and_chapter_inference(tmp_path: Path, encoding: str, suffix: str) -> None:
    text = "第一章 初见\n窗外有雨。\n\n第二章 归来\n灯还亮着。"
    path = tmp_path / f"book-{suffix}.txt"
    path.write_bytes(text.encode(encoding))
    book = parse_book(path)
    assert [chapter.title for chapter in book.chapters] == ["第一章 初见", "第二章 归来"]
    assert "窗外有雨" in book.chapters[0].segments[0].display_text


def test_reparse_keeps_all_segment_ids_stable(tmp_path: Path) -> None:
    path = make_epub3_nav(tmp_path / "stable.epub")
    first = parse_book(path)
    second = parse_book(path)
    first_ids = [segment.segment_id for chapter in first.chapters for segment in chapter.segments]
    second_ids = [segment.segment_id for chapter in second.chapters for segment in chapter.segments]
    assert first_ids == second_ids
    assert first_ids


@pytest.mark.parametrize(
    ("builder", "code"),
    [
        (make_drm_epub, "DRM_NOT_SUPPORTED"),
        (make_empty_epub, "EMPTY_BOOK"),
        (make_traversal_epub, "PATH_TRAVERSAL"),
    ],
)
def test_rejects_unsafe_epubs(tmp_path: Path, builder, code: str) -> None:
    with pytest.raises(BookParseError, match=".+") as error:
        parse_book(builder(tmp_path / f"{code}.epub"))
    assert error.value.code == code


def test_rejects_zip_bomb_ratio(tmp_path: Path) -> None:
    path = make_high_ratio_epub(tmp_path / "bomb.epub")
    with pytest.raises(BookParseError) as error:
        parse_book(path, limits=ParserLimits(max_compression_ratio=20))
    assert error.value.code == "ZIP_BOMB"


def test_rejects_bad_txt_encoding(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\x81")
    with pytest.raises(BookParseError) as error:
        parse_book(path)
    assert error.value.code == "BAD_ENCODING"


def test_rejects_extension_content_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "fake.epub"
    path.write_text("这不是 EPUB", encoding="utf-8")
    with pytest.raises(BookParseError) as error:
        parse_book(path)
    assert error.value.code == "BAD_MAGIC"
