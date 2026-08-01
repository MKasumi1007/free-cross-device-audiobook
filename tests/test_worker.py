from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from audiobook_core.models import Chapter, ParsedBook, PublicationMode
from mac_agent.firebase_rest import FirebaseRestError
from mac_agent.generation import PublishedAsset
from mac_agent.task_cloud import CloudTask
from mac_agent import worker as worker_module
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

    def next_book_deletion(self, owner_uid: str) -> None:
        assert owner_uid == "owner"
        return None

    def next_task(self, owner_uid: str) -> None:
        self.next_task_calls += 1
        raise AssertionError(f"paused worker queried tasks for {owner_uid}")


class FakePausedPolicy:
    @staticmethod
    def pause_reason() -> str:
        return "USER_PAUSED"


class FakeSyncLocalTasks:
    def __init__(self, task_id: str, root: Path) -> None:
        self.task_id = task_id
        self.audio = root / "audio.m4a"
        self.timeline = root / "timeline.json.gz"
        self.audio.write_bytes(b"audio")
        self.timeline.write_bytes(b"timeline")
        self.sync_events: list[str] = []

    def next_task(self) -> None:
        return None

    def task(self, task_id: str) -> dict[str, str]:
        assert task_id == self.task_id
        return {"status": "READY", "sync_status": "PENDING"}

    def asset_path(self, task_id: str, kind: str) -> Path:
        assert task_id == self.task_id
        return self.audio if kind == "audio" else self.timeline

    def mark_syncing(self, _task_id: str) -> None:
        self.sync_events.append("SYNCING")

    def mark_sync_pending(self, _task_id: str, _message: str) -> None:
        self.sync_events.append("PENDING")

    def mark_synced(self, _task_id: str) -> None:
        self.sync_events.append("SYNCED")


class FakeSyncTasks:
    def __init__(self, task: CloudTask) -> None:
        self.task = task
        self.client = object()
        self.ready_records = []

    def active_owner(self) -> str:
        return self.task.owner_uid

    def touch_presence(self) -> None:
        return None

    def next_deletion(self, _owner_uid: str) -> None:
        return None

    def next_book_deletion(self, _owner_uid: str) -> None:
        return None

    def next_task(self, _owner_uid: str) -> CloudTask:
        return self.task

    def claim(self, task: CloudTask) -> CloudTask:
        return replace(
            task,
            status="LEASED",
            attempt_id=task.attempt_id + 1,
            lease_token="lease-token",
            lease_deadline=datetime.now(UTC) + timedelta(minutes=20),
        )

    def transition(self, task: CloudTask, status: str, **changes) -> CloudTask:
        allowed = {name: value for name, value in changes.items() if hasattr(task, name)}
        return replace(task, status=status, **allowed)

    def record_ready(self, task, job, published) -> None:
        self.ready_records.append((task, job, published))


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


def test_full_cache_does_not_block_upload_of_ready_local_audio(
    monkeypatch,
    tmp_path: Path,
) -> None:
    private = make_book("private", PublicationMode.LOCAL_ONLY)
    cloud_task = CloudTask(
        owner_uid="owner",
        task_id="task-ready-local",
        book_id=private.book_id,
        status="QUEUED",
        priority=1,
        attempt_id=0,
        deletion_generation=0,
        start_segment_id=None,
        target_seconds=600,
        voice_version="voice-1",
        storage_mode="PRIVATE_FIRESTORE",
    )
    tasks = FakeSyncTasks(cloud_task)
    local_tasks = FakeSyncLocalTasks(cloud_task.task_id, tmp_path)
    worker = MacGenerationWorker(
        tasks=tasks,  # type: ignore[arg-type]
        library=FakeLibrary({private.book_id: private}),  # type: ignore[arg-type]
        voices=SimpleNamespace(load=lambda: SimpleNamespace(
            confirmed=True,
            voice_version="voice-1",
        )),  # type: ignore[arg-type]
        policy=SimpleNamespace(pause_reason=lambda: None),  # type: ignore[arg-type]
        generator_factory=lambda _profile: object(),  # type: ignore[arg-type,return-value]
        work_root=tmp_path / "work",
        repository="owner/repository",
        local_tasks=local_tasks,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(worker, "_periodic_cleanup", lambda: None)
    monkeypatch.setattr(worker, "_periodic_retention", lambda _owner_uid: None)
    monkeypatch.setattr(worker, "_ensure_book_text", lambda *_args: None)
    monkeypatch.setattr(worker, "_publish_local_ready", lambda *_args: True)
    monkeypatch.setattr(
        worker,
        "_audio_cache_is_full",
        lambda _owner_uid: (_ for _ in ()).throw(AssertionError("capacity gate called")),
    )

    assert worker.run_once() is True
    assert worker.last_state == "LOCAL_SYNCED"


def test_local_sync_enters_uploading_before_recording_ready(
    monkeypatch,
    tmp_path: Path,
) -> None:
    private = make_book("private", PublicationMode.LOCAL_ONLY)
    task = CloudTask(
        owner_uid="owner",
        task_id="task-ready-local",
        book_id=private.book_id,
        status="GENERATING",
        priority=1,
        attempt_id=1,
        deletion_generation=0,
        start_segment_id=None,
        target_seconds=600,
        voice_version="voice-1",
        storage_mode="PRIVATE_FIRESTORE",
        lease_token="lease-token",
    )
    tasks = FakeSyncTasks(task)
    local_tasks = FakeSyncLocalTasks(task.task_id, tmp_path)
    worker = MacGenerationWorker(
        tasks=tasks,  # type: ignore[arg-type]
        library=FakeLibrary({private.book_id: private}),  # type: ignore[arg-type]
        voices=object(),  # type: ignore[arg-type]
        policy=object(),  # type: ignore[arg-type]
        generator_factory=lambda _profile: object(),  # type: ignore[arg-type,return-value]
        work_root=tmp_path / "work",
        repository="owner/repository",
        local_tasks=local_tasks,  # type: ignore[arg-type]
    )
    job = SimpleNamespace(chunk_id="chunk-local")
    monkeypatch.setattr(worker, "_make_job", lambda *_args: job)

    class FakePrivatePublisher:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def publish(self, _book_id: str, path: Path, _name: str) -> PublishedAsset:
            digest = "a" * 64 if path == local_tasks.audio else "b" * 64
            return PublishedAsset(
                asset_id=1,
                name=path.name,
                url="",
                byte_size=path.stat().st_size,
                sha256=digest,
                storage_mode="PRIVATE_FIRESTORE",
                private_key=digest,
                part_count=1,
            )

    monkeypatch.setattr(worker_module, "FirestorePrivateAssetPublisher", FakePrivatePublisher)
    states: list[str] = []
    fence = SimpleNamespace(task=task)

    def record_state(status: str) -> None:
        states.append(status)
        fence.task = replace(fence.task, status=status)

    fence.state = record_state

    assert worker._publish_local_ready(
        task,
        private,
        SimpleNamespace(voice_version="voice-1"),  # type: ignore[arg-type]
        fence,  # type: ignore[arg-type]
    ) is True
    assert states == ["ENCODING", "UPLOADING", "READY"]
    assert local_tasks.sync_events == ["SYNCING", "SYNCED"]
    assert len(tasks.ready_records) == 1


def test_quota_failures_use_progressive_backoff_without_disabling_local_work(
    tmp_path: Path,
) -> None:
    worker = MacGenerationWorker(
        tasks=object(),  # type: ignore[arg-type]
        library=FakeLibrary({}),  # type: ignore[arg-type]
        voices=object(),  # type: ignore[arg-type]
        policy=object(),  # type: ignore[arg-type]
        generator_factory=lambda _profile: object(),  # type: ignore[arg-type,return-value]
        work_root=tmp_path,
        repository="owner/repository",
    )
    quota = FirebaseRestError(
        "quota",
        code="FIREBASE_QUOTA_EXHAUSTED",
        details={"http_status": 429},
    )

    assert worker._is_quota_error(quota) is True
    observed = []
    for _ in range(5):
        worker._activate_cloud_backoff()
        observed.append(worker._cloud_backoff_seconds)

    assert observed == [300, 1_800, 7_200, 21_600, 21_600]
