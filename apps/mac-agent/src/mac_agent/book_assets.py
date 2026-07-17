from __future__ import annotations

import gzip
import json
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from audiobook_core.models import ParsedBook, PublicationMode

from .generation import GenerationError, PublishedAsset


class DataPublisher(Protocol):
    def publish(self, book_id: str, path: Path, asset_name: str) -> PublishedAsset: ...


class BookTextPublisher:
    def __init__(
        self,
        root: Path,
        publisher: DataPublisher,
        *,
        allow_local_only: bool = False,
    ) -> None:
        self.root = root
        self.publisher = publisher
        self.allow_local_only = allow_local_only

    def publish(self, book: ParsedBook) -> PublishedAsset:
        if (
            book.publication_mode is not PublicationMode.PUBLIC_RIGHTS_CONFIRMED
            and not self.allow_local_only
        ):
            raise GenerationError("RIGHTS_NOT_CONFIRMED", "未确认传播权的书不能发布正文。")
        if (
            book.publication_mode is PublicationMode.LOCAL_ONLY
            and getattr(self.publisher, "storage_mode", "PUBLIC_GITHUB") != "PRIVATE_FIRESTORE"
        ):
            raise GenerationError(
                "PRIVATE_STORAGE_REQUIRED",
                "私有书正文只能保存到账号私有区。",
            )
        work = self.root / book.book_id
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = work / "book-text.json.gz"
        payload = json.dumps(
            book.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        with path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as archive:
                archive.write(payload)
        path.chmod(0o600)
        digest = self._hash_file(path)
        name = f"book-{book.book_id}-text-{digest[:12]}.json.gz"
        try:
            return self.publisher.publish(book.book_id, path, name)
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
