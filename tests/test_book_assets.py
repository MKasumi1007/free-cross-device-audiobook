from __future__ import annotations

import gzip
import json
from hashlib import sha256
from pathlib import Path

import pytest

from audiobook_core.parser import parse_book
from mac_agent.book_assets import BookTextPublisher
from mac_agent.generation import GenerationError, PublishedAsset


class FakePublisher:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, bytes]] = []

    def publish(self, book_id: str, path: Path, asset_name: str) -> PublishedAsset:
        content = path.read_bytes()
        self.uploads.append((book_id, asset_name, content))
        return PublishedAsset(
            asset_id=len(self.uploads),
            name=asset_name,
            url=f"https://example.test/{asset_name}",
            byte_size=len(content),
            sha256=sha256(content).hexdigest(),
        )


def test_rights_confirmed_book_text_is_deterministic_and_has_no_local_path(tmp_path: Path) -> None:
    source = tmp_path / "public-domain.txt"
    source.write_text("第一章\n这是项目自制的公开测试正文。", encoding="utf-8")
    book = parse_book(source, rights_confirmed=True)
    remote = FakePublisher()
    service = BookTextPublisher(tmp_path / "work", remote)

    first = service.publish(book)
    second = service.publish(book)

    assert first.name == second.name
    assert not (tmp_path / "work" / book.book_id / "book-text.json.gz").exists()
    payload = json.loads(gzip.decompress(remote.uploads[0][2]))
    assert payload["book_id"] == book.book_id
    assert str(source) not in json.dumps(payload, ensure_ascii=False)


def test_local_only_book_text_never_reaches_publisher(tmp_path: Path) -> None:
    source = tmp_path / "private.txt"
    source.write_text("仅本机测试正文。", encoding="utf-8")
    book = parse_book(source, rights_confirmed=False)
    remote = FakePublisher()
    with pytest.raises(GenerationError) as error:
        BookTextPublisher(tmp_path / "work", remote).publish(book)
    assert error.value.code == "RIGHTS_NOT_CONFIRMED"
    assert remote.uploads == []
