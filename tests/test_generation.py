from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from audiobook_core.models import PublicationMode
from mac_agent.generation import (
    ChunkJob,
    ChunkPipeline,
    GeneratedAudio,
    GenerationError,
    PublishedAsset,
    SegmentJob,
)


class FakeGenerator:
    def __init__(self, fail_on_call: int | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on_call = fail_on_call
        self.unloads = 0

    def generate(self, text: str, output_wav: Path, on_progress=None) -> GeneratedAudio:
        self.calls.append(text)
        if self.fail_on_call == len(self.calls):
            raise GenerationError("NETWORK_INTERRUPTED", "simulated")
        output_wav.write_bytes(f"wav:{text}".encode())
        if on_progress:
            on_progress(1, 1, 2.5)
        return GeneratedAudio(duration_seconds=2.5, sample_rate=24000)

    def unload(self) -> None:
        self.unloads += 1


class FakeEncoder:
    def encode(self, wav_paths: list[Path], destination: Path) -> float:
        destination.write_bytes(b"".join(path.read_bytes() for path in wav_paths))
        return len(wav_paths) * 2.5


class FakePublisher:
    def __init__(self, *, reuse_first: bool = False, fail_second: bool = False) -> None:
        self.published: list[tuple[str, str]] = []
        self.deleted: list[int] = []
        self.next_id = 10
        self.reuse_first = reuse_first
        self.fail_second = fail_second

    def publish(self, book_id: str, path: Path, asset_name: str) -> PublishedAsset:
        self.published.append((book_id, asset_name))
        if self.fail_second and len(self.published) == 2:
            raise GenerationError("GITHUB_UPLOAD_FAILED", "simulated")
        content = path.read_bytes()
        asset = PublishedAsset(
            asset_id=self.next_id,
            name=asset_name,
            url=f"https://example.test/{asset_name}",
            byte_size=len(content),
            sha256=sha256(content).hexdigest(),
            created=not (self.reuse_first and len(self.published) == 1),
        )
        self.next_id += 1
        return asset

    def delete(self, _book_id: str, asset_id: int) -> None:
        self.deleted.append(asset_id)


class FakePrivatePublisher(FakePublisher):
    storage_mode = "PRIVATE_FIRESTORE"

    def publish(self, book_id: str, path: Path, asset_name: str) -> PublishedAsset:
        asset = super().publish(book_id, path, asset_name)
        return replace(
            asset,
            url="",
            storage_mode=self.storage_mode,
            private_key=asset.sha256,
            part_count=1,
        )


class FakeFence:
    def __init__(self, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def assert_current(self, _job: ChunkJob) -> None:
        self.calls += 1
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise GenerationError("STALE_LEASE", "simulated stale lease")


def make_job(mode: PublicationMode = PublicationMode.PUBLIC_RIGHTS_CONFIRMED) -> ChunkJob:
    segments = tuple(
        SegmentJob(
            segment_id=f"segment-{index}",
            chapter_id="chapter-1",
            order=index,
            spoken_text=f"第{index}段测试文字",
            text_hash=sha256(f"text-{index}".encode()).hexdigest(),
        )
        for index in range(3)
    )
    return ChunkJob(
        task_id="task-1",
        book_id="book-1",
        chunk_id="chunk-1",
        chapter_id="chapter-1",
        publication_mode=mode,
        voice_version="voice-1",
        attempt_id=1,
        lease_token="secure-lease-token",
        deletion_generation=0,
        segments=segments,
    )


def test_interrupted_generation_resumes_from_atomic_segment_checkpoint(tmp_path: Path) -> None:
    first = FakeGenerator(fail_on_call=2)
    publisher = FakePublisher()
    pipeline = ChunkPipeline(
        tmp_path,
        first,
        publisher,
        FakeFence(),
        encoder=FakeEncoder(),
    )
    with pytest.raises(GenerationError):
        pipeline.run(make_job())

    checkpoint = tmp_path / "tasks/task-1/chunk-1/checkpoint.json"
    assert checkpoint.exists()
    assert (tmp_path / "tasks/task-1/chunk-1/segments/segment-0.wav").exists()

    second = FakeGenerator()
    resumed = ChunkPipeline(
        tmp_path,
        second,
        publisher,
        FakeFence(),
        encoder=FakeEncoder(),
    ).run(make_job())
    assert second.calls == ["第1段测试文字", "第2段测试文字"]
    assert resumed.duration_seconds == 7.5
    assert len(publisher.published) == 2
    assert checkpoint.exists()
    assert not (tmp_path / "tasks/task-1/chunk-1/chunk.m4a").exists()
    assert not (tmp_path / "tasks/task-1/chunk-1/timeline.json.gz").exists()
    assert not (tmp_path / "tasks/task-1/chunk-1/segments").exists()


def test_published_receipt_makes_repeated_dispatch_idempotent(tmp_path: Path) -> None:
    generator = FakeGenerator()
    publisher = FakePublisher()
    pipeline = ChunkPipeline(
        tmp_path,
        generator,
        publisher,
        FakeFence(),
        encoder=FakeEncoder(),
    )
    first = pipeline.run(make_job())
    repeated = pipeline.run(make_job())
    assert first.reused is False
    assert repeated.reused is True
    assert len(publisher.published) == 2
    assert len(generator.calls) == 3


def test_stale_lease_after_upload_rolls_back_late_assets(tmp_path: Path) -> None:
    publisher = FakePublisher()
    pipeline = ChunkPipeline(
        tmp_path,
        FakeGenerator(),
        publisher,
        FakeFence(fail_after=7),
        encoder=FakeEncoder(),
    )
    with pytest.raises(GenerationError) as error:
        pipeline.run(make_job())
    assert error.value.code == "STALE_LEASE"
    assert publisher.deleted == [10, 11]
    assert not (tmp_path / "tasks/task-1/chunk-1/published.json").exists()


def test_local_only_book_never_reaches_public_publisher(tmp_path: Path) -> None:
    generator = FakeGenerator()
    publisher = FakePublisher()
    pipeline = ChunkPipeline(
        tmp_path,
        generator,
        publisher,
        FakeFence(),
        encoder=FakeEncoder(),
    )
    with pytest.raises(GenerationError) as error:
        pipeline.run(make_job(PublicationMode.LOCAL_ONLY))
    assert error.value.code == "PRIVATE_STORAGE_REQUIRED"
    assert not generator.calls
    assert not publisher.published


def test_local_only_book_can_generate_only_with_private_publisher(tmp_path: Path) -> None:
    generator = FakeGenerator()
    publisher = FakePrivatePublisher()
    pipeline = ChunkPipeline(
        tmp_path,
        generator,
        publisher,
        FakeFence(),
        encoder=FakeEncoder(),
    )

    result = pipeline.run(make_job(PublicationMode.LOCAL_ONLY))

    assert result.duration_seconds == 7.5
    assert len(publisher.published) == 2
    assert generator.calls


def test_stale_process_lock_is_recovered_after_restart(tmp_path: Path) -> None:
    (tmp_path / "active-task.lock").write_text("99999999", encoding="ascii")
    pipeline = ChunkPipeline(
        tmp_path,
        FakeGenerator(),
        FakePublisher(),
        FakeFence(),
        encoder=FakeEncoder(),
    )
    assert pipeline.run(make_job()).duration_seconds == 7.5
    assert not (tmp_path / "active-task.lock").exists()


def test_rollback_does_not_delete_a_verified_preexisting_asset(tmp_path: Path) -> None:
    publisher = FakePublisher(reuse_first=True, fail_second=True)
    pipeline = ChunkPipeline(
        tmp_path,
        FakeGenerator(),
        publisher,
        FakeFence(),
        encoder=FakeEncoder(),
    )
    with pytest.raises(GenerationError):
        pipeline.run(make_job())
    assert publisher.deleted == []
