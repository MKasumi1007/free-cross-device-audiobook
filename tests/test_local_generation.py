from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from audiobook_core.models import Chapter, ParsedBook, PublicationMode, TextSegment
from mac_agent.generation import ChunkJob, PublishedChunk, SegmentJob
from mac_agent.local_generation import LocalAssetPublisher, LocalGenerationStore


def make_book() -> ParsedBook:
    segments = tuple(
        TextSegment(
            segment_id=f"segment-{index}",
            chapter_id="chapter-1",
            order=index,
            display_text=f"第 {index + 1} 段",
            spoken_text="这是用于检查本地生成队列的文字。",
            text_hash=sha256(f"segment-{index}".encode()).hexdigest(),
        )
        for index in range(2)
    )
    return ParsedBook(
        book_id="book-1",
        title="本地生成测试",
        author="测试",
        source_format="TXT",
        source_sha256="a" * 64,
        publication_mode=PublicationMode.LOCAL_ONLY,
        chapters=(Chapter("chapter-1", 0, "第一章", "book.txt", segments),),
    )


def test_local_queue_is_persistent_and_uses_cloud_compatible_task_ids(tmp_path: Path) -> None:
    store = LocalGenerationStore(tmp_path / "local")

    result = store.enqueue("owner-1", make_book(), ["chapter-1"], "voice-1")
    task = store.next_task()

    assert result == {"chapters": 1, "created": 1, "resumed": 0, "unchanged": 0}
    assert task is not None
    assert task["task_id"] == "chunk-book-1-voice-1-segment-0"
    assert task["execution_mode"] == "LOCAL"
    assert LocalGenerationStore(tmp_path / "local").next_task() == task


def test_local_queue_can_take_over_only_unfinished_cloud_chunks(tmp_path: Path) -> None:
    store = LocalGenerationStore(tmp_path / "local")
    book = make_book()
    pending_task_id = "chunk-book-1-voice-1-segment-0"

    result = store.enqueue(
        "owner-1",
        book,
        ["chapter-1"],
        "voice-1",
        [pending_task_id],
    )

    assert result == {"chapters": 1, "created": 1, "resumed": 0, "unchanged": 0}
    assert store.next_task() is not None
    assert store.next_task()["task_id"] == pending_task_id  # type: ignore[index]


def test_local_queue_supports_pause_resume_and_reorder(tmp_path: Path) -> None:
    store = LocalGenerationStore(tmp_path / "local")
    store.enqueue("owner-1", make_book(), ["chapter-1"], "voice-1")
    task_id = str(store.next_task()["task_id"])  # type: ignore[index]

    assert store.act([task_id], "PAUSE") == 1
    assert store.next_task() is None
    assert store.act([task_id], "RESUME") == 1
    assert store.reorder([task_id]) == 1
    assert store.next_task() is not None


def test_interrupted_active_task_is_recovered_after_agent_restart(tmp_path: Path) -> None:
    root = tmp_path / "local"
    store = LocalGenerationStore(root)
    store.enqueue("owner-1", make_book(), ["chapter-1"], "voice-1")
    pending = store.next_task()
    assert pending is not None
    store.claim(str(pending["task_id"]))

    recovered = LocalGenerationStore(root).next_task()

    assert recovered is not None
    assert recovered["status"] == "FAILED_RETRYABLE"
    assert recovered["error_code"] == "LOCAL_AGENT_RESTARTED"


def test_ready_local_audio_is_indexed_for_loopback_playback(tmp_path: Path) -> None:
    store = LocalGenerationStore(tmp_path / "local")
    store.enqueue("owner-1", make_book(), ["chapter-1"], "voice-1")
    pending = store.next_task()
    assert pending is not None
    claimed = store.claim(str(pending["task_id"]))
    publisher = LocalAssetPublisher(store.assets_root, str(claimed["task_id"]))
    audio_source = tmp_path / "source.m4a"
    timeline_source = tmp_path / "timeline.json.gz"
    audio_source.write_bytes(b"audio")
    timeline_source.write_bytes(b"timeline")
    audio = publisher.publish("book-1", audio_source, "chunk.m4a")
    timeline = publisher.publish("book-1", timeline_source, "timeline.json.gz")
    segments = tuple(
        SegmentJob(
            segment_id=item.segment_id,
            chapter_id=item.chapter_id,
            order=item.order,
            spoken_text=item.spoken_text,
            text_hash=item.text_hash,
        )
        for item in make_book().chapters[0].segments
    )
    job = ChunkJob(
        task_id=str(claimed["task_id"]),
        book_id="book-1",
        chunk_id="chunk-00000-segment-",
        chapter_id="chapter-1",
        publication_mode=PublicationMode.LOCAL_ONLY,
        voice_version="voice-1",
        attempt_id=int(claimed["attempt_id"]),
        lease_token=str(claimed["lease_token"]),
        deletion_generation=0,
        segments=segments,
    )
    store.record_ready(
        str(claimed["task_id"]),
        job,
        PublishedChunk("chunk-00000-segment-", 5.0, audio, timeline),
    )

    status = store.status()
    assert status["pending_sync"] == 1
    assert status["audio_chunks"][0]["storage_mode"] == "LOCAL_MAC"
    assert status["audio_chunks"][0]["asset_url"].endswith("/audio")
    assert store.asset_path(str(claimed["task_id"]), "audio") is not None
