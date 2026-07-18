from __future__ import annotations

from pathlib import Path

from audiobook_core.models import Chapter, ParsedBook, PublicationMode
from mac_agent.worker import MacGenerationWorker


def make_book(book_id: str, mode: PublicationMode) -> ParsedBook:
    return ParsedBook(
        book_id=book_id,
        title=book_id,
        author="",
        source_format="TXT",
        source_sha256=book_id.ljust(64, "0")[:64],
        publication_mode=mode,
        chapters=(
            Chapter(
                chapter_id=f"{book_id}-chapter",
                order=0,
                title="第一章",
                source_href="chapter.txt",
                segments=(),
            ),
        ),
    )


class FakeLibrary:
    def __init__(self, books: dict[str, ParsedBook]) -> None:
        self.books = books

    def book_ids(self) -> list[str]:
        return list(self.books)

    def get(self, book_id: str) -> ParsedBook | None:
        return self.books.get(book_id)


def test_idle_sync_only_refreshes_private_books(monkeypatch, tmp_path: Path) -> None:
    public = make_book("public", PublicationMode.PUBLIC_RIGHTS_CONFIRMED)
    private = make_book("private", PublicationMode.LOCAL_ONLY)
    library = FakeLibrary({public.book_id: public, private.book_id: private})
    worker = MacGenerationWorker(
        tasks=object(),  # type: ignore[arg-type]
        library=library,  # type: ignore[arg-type]
        voices=object(),  # type: ignore[arg-type]
        policy=object(),  # type: ignore[arg-type]
        generator_factory=lambda _profile: object(),  # type: ignore[arg-type,return-value]
        work_root=tmp_path,
        repository="owner/repository",
    )
    synced: list[tuple[str, str, str]] = []

    def record(owner_uid: str, task_id: str, book: ParsedBook) -> None:
        synced.append((owner_uid, task_id, book.book_id))
        worker._published_books.add(book.book_id)

    monkeypatch.setattr(worker, "_ensure_book_text", record)

    assert worker._sync_private_book_texts("owner") is True
    assert synced == [("owner", "library-sync", "private")]
    assert worker._sync_private_book_texts("owner") is False
