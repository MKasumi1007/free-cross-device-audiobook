from __future__ import annotations

import time
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


class FakeCapacityTasks:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0

    def ready_audio_seconds(self, owner_uid: str, book_ids: list[str]) -> float:
        assert owner_uid == "owner"
        assert book_ids == ["public"]
        self.calls += 1
        return self.seconds


class FakePausedTasks:
    def __init__(self) -> None:
        self.next_task_calls = 0

    def active_owner(self) -> str:
        return "owner"

    def next_deletion(self, owner_uid: str) -> None:
        assert owner_uid == "owner"
        return None

    def next_task(self, owner_uid: str) -> None:
        self.next_task_calls += 1
        raise AssertionError(f"paused worker queried tasks for {owner_uid}")


class FakePausedPolicy:
    @staticmethod
    def pause_reason() -> str:
        return "USER_PAUSED"


def test_global_pause_does_not_claim_or_modify_chapter_tasks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    tasks = FakePausedTasks()
    worker = MacGenerationWorker(
        tasks=tasks,  # type: ignore[arg-type]
        library=FakeLibrary({}),  # type: ignore[arg-type]
        voices=object(),  # type: ignore[arg-type]
        policy=FakePausedPolicy(),  # type: ignore[arg-type]
        generator_factory=lambda _profile: object(),  # type: ignore[arg-type,return-value]
        work_root=tmp_path,
        repository="owner/repository",
    )
    worker._last_presence = time.monotonic()
    monkeypatch.setattr(worker, "_periodic_cleanup", lambda: None)
    monkeypatch.setattr(worker, "_periodic_retention", lambda _owner_uid: None)

    assert worker.run_once() is False
    assert worker.last_state == "USER_PAUSED"
    assert tasks.next_task_calls == 0


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


def test_generation_waits_when_five_hour_audio_cache_is_full(tmp_path: Path) -> None:
    public = make_book("public", PublicationMode.PUBLIC_RIGHTS_CONFIRMED)
    tasks = FakeCapacityTasks(5 * 60 * 60)
    worker = MacGenerationWorker(
        tasks=tasks,  # type: ignore[arg-type]
        library=FakeLibrary({public.book_id: public}),  # type: ignore[arg-type]
        voices=object(),  # type: ignore[arg-type]
        policy=object(),  # type: ignore[arg-type]
        generator_factory=lambda _profile: object(),  # type: ignore[arg-type,return-value]
        work_root=tmp_path,
        repository="owner/repository",
    )

    assert worker._audio_cache_is_full("owner") is True
    assert worker._audio_cache_is_full("owner") is True
    assert tasks.calls == 1
