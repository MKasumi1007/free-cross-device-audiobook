from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from audiobook_core.models import PublicationMode
from audiobook_core.parser import parse_book
from mac_agent.firebase_rest import FirebasePublicConfig, FirebaseRestClient, Identity
from mac_agent.generation import ChunkJob, GenerationError, PublishedAsset, PublishedChunk, SegmentJob
from mac_agent.task_cloud import FirestoreWorkerTasks


class FakeStore:
    def read(self) -> str | None:
        return None

    def write(self, token: str) -> None:
        del token

    def delete(self) -> None:
        return None


class FakeTransport:
    def __init__(self, responses: list[tuple[int, dict[str, Any]]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, bytes]:
        self.requests.append((method, url, headers, body))
        status, value = self.responses.pop(0)
        return status, json.dumps(value).encode()


def firestore_value(value: object) -> dict[str, object]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, datetime):
        return {"timestampValue": value.astimezone(UTC).isoformat()}
    return {"stringValue": str(value)}


def document(path: str, fields: dict[str, object], *, update_time: str = "2026-07-17T09:00:00Z") -> dict[str, object]:
    return {
        "name": f"projects/demo/databases/(default)/documents/{path}",
        "fields": {name: firestore_value(value) for name, value in fields.items()},
        "updateTime": update_time,
    }


def make_client(transport: FakeTransport) -> FirebaseRestClient:
    client = FirebaseRestClient(
        FirebasePublicConfig(api_key="public-key", project_id="demo"),
        token_store=FakeStore(),
        transport=transport,
    )
    client._identity = Identity("private-id-token", "keychain-refresh", "worker-a")
    return client


def test_worker_lists_and_claims_only_metadata_without_book_text() -> None:
    deadline = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    transport = FakeTransport([
        (200, document("workerLinks/worker-a", {"owner_uid": "owner-a", "revoked_at": None})),
        (200, [{"document": document("users/owner-a/generationRequests/task-a", {
            "owner_uid": "owner-a",
            "task_id": "task-a",
            "book_id": "book-a",
            "status": "QUEUED",
            "priority": 300,
            "attempt_id": 0,
            "deletion_generation": 0,
            "start_segment_id": "segment-a",
            "target_seconds": 600,
            "voice_version": "voice-a",
        })}]),
        (200, {"writeResults": [{"updateTime": "2026-07-17T09:01:00Z"}]}),
    ])
    tasks = FirestoreWorkerTasks(make_client(transport))
    assert tasks.active_owner() == "owner-a"
    pending = tasks.next_task("owner-a")
    assert pending is not None
    claimed = tasks.claim(pending)
    assert claimed.attempt_id == 1
    assert claimed.lease_owner == "worker-a"
    assert claimed.lease_deadline is not None
    body = (transport.requests[-1][3] or b"").decode()
    assert "书籍正文" not in body
    assert "private-id-token" not in body
    assert deadline not in body


def test_worker_presence_uses_server_time_without_exposing_tokens() -> None:
    transport = FakeTransport([(200, {"writeResults": [{}]})])
    tasks = FirestoreWorkerTasks(make_client(transport))
    tasks.touch_presence()
    body = (transport.requests[-1][3] or b"").decode()
    assert "last_seen_at" in body
    assert "REQUEST_TIME" in body
    assert "private-id-token" not in body


def test_ready_audio_seconds_counts_only_playable_cache() -> None:
    transport = FakeTransport([
        (200, [
            {"document": document("users/owner-a/books/book-a/audioChunks/chunk-a", {
                "status": "READY",
                "duration_seconds": "7200.5",
            })},
            {"document": document("users/owner-a/books/book-a/audioChunks/chunk-b", {
                "status": "DELETING",
                "duration_seconds": "900",
            })},
            {"document": document("users/owner-a/books/book-a/audioChunks/chunk-c", {
                "status": "READY",
                "duration_seconds": "1800",
            })},
        ]),
    ])
    tasks = FirestoreWorkerTasks(make_client(transport))

    assert tasks.ready_audio_seconds("owner-a", ["book-a"]) == 9000.5


def test_worker_auto_resumes_only_safe_paused_reasons() -> None:
    transport = FakeTransport([
        (200, [
            {"document": document("users/owner-a/generationRequests/user-paused", {
                "task_id": "user-paused",
                "book_id": "book-a",
                "status": "PAUSED",
                "pause_reason": "USER_PAUSED",
                "priority": 400,
                "attempt_id": 1,
                "deletion_generation": 0,
            })},
            {"document": document("users/owner-a/generationRequests/memory-paused", {
                "task_id": "memory-paused",
                "book_id": "book-a",
                "status": "PAUSED",
                "pause_reason": "MEMORY_PRESSURE",
                "priority": 300,
                "attempt_id": 1,
                "deletion_generation": 0,
            })},
        ]),
    ])
    task = FirestoreWorkerTasks(make_client(transport)).next_task("owner-a")
    assert task is not None
    assert task.task_id == "memory-paused"


def test_ready_metadata_is_idempotent_and_bound_to_task_lease() -> None:
    transport = FakeTransport([
        (404, {}),
        (200, {"writeResults": [{"updateTime": "2026-07-17T09:02:00Z"}]}),
    ])
    tasks = FirestoreWorkerTasks(make_client(transport))
    task = tasks._task("owner-a", document("users/owner-a/generationRequests/task-a", {
        "task_id": "task-a",
        "book_id": "book-a",
        "status": "UPLOADING",
        "priority": 300,
        "attempt_id": 2,
        "deletion_generation": 4,
        "lease_owner": "worker-a",
        "lease_token": "secure-lease",
        "lease_deadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }))
    segment = SegmentJob("segment-a", "chapter-a", 0, "测试", "a" * 64)
    job = ChunkJob(
        "task-a",
        "book-a",
        "chunk-a",
        "chapter-a",
        publication_mode=PublicationMode.PUBLIC_RIGHTS_CONFIRMED,
        voice_version="voice-a",
        attempt_id=2,
        lease_token="secure-lease",
        deletion_generation=4,
        segments=(segment,),
    )
    audio = PublishedAsset(10, "audio.m4a", "https://example/audio", 100, "b" * 64)
    timeline = PublishedAsset(11, "timeline.json.gz", "https://example/timeline", 50, "c" * 64)
    tasks.record_ready(task, job, PublishedChunk("chunk-a", 12.5, audio, timeline))
    body = (transport.requests[-1][3] or b"").decode()
    assert '"task_id"' in body
    assert "secure-lease" in body
    assert '"deletion_generation"' in body


def test_private_ready_metadata_never_contains_a_public_url() -> None:
    transport = FakeTransport([
        (404, {}),
        (200, {"writeResults": [{"updateTime": "2026-07-17T09:02:00Z"}]}),
    ])
    tasks = FirestoreWorkerTasks(make_client(transport))
    task = tasks._task("owner-a", document("users/owner-a/generationRequests/task-private", {
        "task_id": "task-private",
        "book_id": "book-private",
        "status": "UPLOADING",
        "priority": 300,
        "attempt_id": 1,
        "deletion_generation": 0,
        "lease_token": "secure-private-lease",
        "storage_mode": "PRIVATE_FIRESTORE",
    }))
    segment = SegmentJob("segment-a", "chapter-a", 0, "测试", "a" * 64)
    job = ChunkJob(
        "task-private", "book-private", "chunk-private", "chapter-a",
        publication_mode=PublicationMode.LOCAL_ONLY,
        voice_version="voice-a", attempt_id=1, lease_token="secure-private-lease",
        deletion_generation=0, segments=(segment,),
    )
    audio = PublishedAsset(
        10, "audio.m4a", "", 100, "b" * 64,
        storage_mode="PRIVATE_FIRESTORE", private_key="c" * 64, part_count=2,
    )
    timeline = PublishedAsset(
        11, "timeline.json.gz", "", 50, "d" * 64,
        storage_mode="PRIVATE_FIRESTORE", private_key="e" * 64, part_count=1,
    )

    tasks.record_ready(task, job, PublishedChunk("chunk-private", 12.5, audio, timeline))

    body = (transport.requests[-1][3] or b"").decode()
    assert '"storage_mode":{"stringValue":"PRIVATE_FIRESTORE"}' in body
    assert '"asset_url":{"nullValue":null}' in body
    assert '"private_audio_key":{"stringValue":"' + "c" * 64 + '"}' in body
    assert "https://" not in body


def test_existing_matching_ready_metadata_does_not_write_again() -> None:
    transport = FakeTransport([
        (200, document("users/owner-a/books/book-a/audioChunks/chunk-a", {
            "status": "READY",
            "asset_id": 10,
            "sha256": "b" * 64,
            "timeline_asset_id": 11,
        })),
    ])
    tasks = FirestoreWorkerTasks(make_client(transport))
    task = tasks._task("owner-a", document("users/owner-a/generationRequests/task-a", {
        "task_id": "task-a",
        "book_id": "book-a",
        "status": "UPLOADING",
        "priority": 300,
        "attempt_id": 2,
        "deletion_generation": 4,
    }))
    segment = SegmentJob("segment-a", "chapter-a", 0, "测试", "a" * 64)
    job = ChunkJob(
        "task-a", "book-a", "chunk-a", "chapter-a",
        publication_mode=PublicationMode.PUBLIC_RIGHTS_CONFIRMED,
        voice_version="voice-a", attempt_id=2, lease_token="secure", deletion_generation=4,
        segments=(segment,),
    )
    audio = PublishedAsset(10, "audio.m4a", "url", 100, "b" * 64)
    timeline = PublishedAsset(11, "timeline", "url", 50, "c" * 64)
    tasks.record_ready(task, job, PublishedChunk("chunk-a", 12.5, audio, timeline))
    assert len(transport.requests) == 1


def test_failed_playback_metadata_can_be_repaired_by_current_lease() -> None:
    transport = FakeTransport([
        (200, document("users/owner-a/books/book-a/audioChunks/chunk-a", {
            "status": "FAILED_RETRYABLE",
            "task_id": "task-a",
            "deletion_generation": 4,
        })),
        (200, {"writeResults": [{"updateTime": "2026-07-17T09:03:00Z"}]}),
    ])
    tasks = FirestoreWorkerTasks(make_client(transport))
    task = tasks._task("owner-a", document("users/owner-a/generationRequests/task-a", {
        "task_id": "task-a",
        "book_id": "book-a",
        "status": "UPLOADING",
        "priority": 300,
        "attempt_id": 3,
        "deletion_generation": 4,
        "lease_token": "secure-lease",
    }))
    segment = SegmentJob("segment-a", "chapter-a", 0, "测试", "a" * 64)
    job = ChunkJob(
        "task-a", "book-a", "chunk-a", "chapter-a",
        publication_mode=PublicationMode.PUBLIC_RIGHTS_CONFIRMED,
        voice_version="voice-a", attempt_id=3, lease_token="secure-lease",
        deletion_generation=4, segments=(segment,),
    )
    audio = PublishedAsset(20, "audio.m4a", "url", 100, "b" * 64)
    timeline = PublishedAsset(21, "timeline", "url", 50, "c" * 64)
    tasks.record_ready(task, job, PublishedChunk("chunk-a", 12.5, audio, timeline))
    body = (transport.requests[-1][3] or b"").decode()
    assert '"asset_id":{"integerValue":"20"}' in body
    assert '"updateTime":"2026-07-17T09:00:00Z"' in body


def test_book_text_asset_metadata_is_reused_and_recorded(tmp_path: Path) -> None:
    transport = FakeTransport([
        (200, document("users/owner-a/books/book-a", {
            "text_status": "READY",
            "text_asset_id": 30,
            "text_asset_name": "book-text.json.gz",
            "text_asset_url": "https://example.test/book-text.json.gz",
            "text_sha256": "d" * 64,
            "text_byte_size": 400,
        })),
        (200, {"writeResults": [{}]}),
    ])
    tasks = FirestoreWorkerTasks(make_client(transport))
    existing = tasks.book_text_asset("owner-a", "book-a")
    assert existing is not None
    assert existing.asset_id == 30
    assert existing.created is False

    source = tmp_path / "book.txt"
    source.write_text("项目自制正文。", encoding="utf-8")
    book = parse_book(source, rights_confirmed=True)
    tasks.record_book_text("owner-a", book, existing)
    body = (transport.requests[-1][3] or b"").decode()
    assert "text_asset_url" in body
    assert "private-id-token" not in body


def test_private_book_text_pointer_is_reused_without_public_url() -> None:
    transport = FakeTransport([
        (200, document("users/owner-a/books/book-private", {
            "text_status": "READY",
            "private_text_key": "f" * 64,
            "private_text_name": "book-private-text.json.gz",
            "private_text_sha256": "a" * 64,
            "private_text_byte_size": 400,
            "private_text_parts": 1,
        })),
    ])

    asset = FirestoreWorkerTasks(make_client(transport)).book_text_asset(
        "owner-a",
        "book-private",
    )

    assert asset is not None
    assert asset.storage_mode == "PRIVATE_FIRESTORE"
    assert asset.private_key == "f" * 64
    assert asset.url == ""


def test_deletion_is_claimed_and_completed_with_metadata_barrier() -> None:
    deletion_document = document("users/owner-a/audioDeletionRequests/delete-a", {
        "owner_uid": "owner-a",
        "request_id": "delete-a",
        "book_id": "book-a",
        "chunk_id": "chunk-a",
        "task_id": "task-a",
        "status": "QUEUED",
        "attempt_count": 0,
        "deletion_generation": 2,
        "asset_id": 100,
        "asset_url": "https://example/audio",
        "timeline_asset_id": 200,
        "timeline_url": "https://example/timeline",
    })
    transport = FakeTransport([
        (200, [{"document": deletion_document}]),
        (200, {"writeResults": [{"updateTime": "2026-07-17T09:01:00Z"}]}),
        (200, document("users/owner-a/books/book-a/audioChunks/chunk-a", {
            "status": "DELETING",
            "deletion_request_id": "delete-a",
            "deletion_generation": 2,
        })),
        (200, {"writeResults": [{}, {}]}),
    ])
    tasks = FirestoreWorkerTasks(make_client(transport))
    pending = tasks.next_deletion("owner-a")
    assert pending is not None
    claimed = tasks.claim_deletion(pending)
    assert claimed.status == "PROCESSING"
    assert claimed.attempt_count == 1
    tasks.complete_deletion(claimed)
    body = (transport.requests[-1][3] or b"").decode()
    assert '"status":{"stringValue":"DONE"}' in body
    assert '"asset_id":{"nullValue":null}' in body
    assert "private-id-token" not in body


def test_expired_deletion_lease_is_recovered_after_restart() -> None:
    expired = datetime.now(UTC) - timedelta(minutes=1)
    transport = FakeTransport([
        (200, []),
        (200, [{"document": document("users/owner-a/audioDeletionRequests/delete-a", {
            "request_id": "delete-a",
            "book_id": "book-a",
            "chunk_id": "chunk-a",
            "task_id": "task-a",
            "status": "PROCESSING",
            "attempt_count": 1,
            "deletion_generation": 2,
            "lease_deadline": expired,
        })}]),
    ])
    recovered = FirestoreWorkerTasks(make_client(transport)).next_deletion("owner-a")
    assert recovered is not None
    assert recovered.request_id == "delete-a"
    assert recovered.lease_deadline == expired


def test_worker_queues_only_audio_older_than_five_days() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=UTC)
    old_chunk = document("users/owner-a/books/book-a/audioChunks/chunk-old", {
        "owner_uid": "owner-a",
        "task_id": "task-old",
        "book_id": "book-a",
        "chunk_id": "chunk-old",
        "status": "READY",
        "completed_at": now - timedelta(days=6),
        "deletion_generation": 2,
        "storage_mode": "PRIVATE_FIRESTORE",
        "asset_id": None,
        "asset_url": None,
        "timeline_asset_id": None,
        "timeline_url": None,
        "private_audio_key": "a" * 64,
        "private_timeline_key": "b" * 64,
        "private_audio_parts": 3,
        "private_timeline_parts": 1,
    })
    recent_chunk = document("users/owner-a/books/book-a/audioChunks/chunk-recent", {
        "owner_uid": "owner-a",
        "task_id": "task-recent",
        "book_id": "book-a",
        "chunk_id": "chunk-recent",
        "status": "READY",
        "completed_at": now - timedelta(days=2),
        "deletion_generation": 0,
    })
    transport = FakeTransport([
        (200, [{"document": recent_chunk}, {"document": old_chunk}]),
        (200, {"writeResults": [{}, {}]}),
    ])

    queued = FirestoreWorkerTasks(make_client(transport)).queue_expired_audio(
        "owner-a",
        ["book-a"],
        now=now,
    )

    assert queued == 1
    body = (transport.requests[-1][3] or b"").decode()
    assert "AUTO_RETENTION_5_DAYS" in body
    assert "chunk-old" in body
    assert "chunk-recent" not in body
    assert '"deletion_generation":{"integerValue":"3"}' in body
    assert '"private_audio_key":{"stringValue":"' + "a" * 64 + '"}' in body
    assert "REQUEST_TIME" in body
    assert "private-id-token" not in body


def test_retention_scan_does_not_queue_recent_audio() -> None:
    now = datetime(2026, 7, 17, 12, tzinfo=UTC)
    transport = FakeTransport([
        (200, [{"document": document(
            "users/owner-a/books/book-a/audioChunks/chunk-recent",
            {
                "owner_uid": "owner-a",
                "task_id": "task-recent",
                "book_id": "book-a",
                "chunk_id": "chunk-recent",
                "status": "READY",
                "completed_at": now - timedelta(days=4),
                "deletion_generation": 0,
            },
        )}]),
    ])

    assert FirestoreWorkerTasks(make_client(transport)).queue_expired_audio(
        "owner-a",
        ["book-a"],
        now=now,
    ) == 0
    assert len(transport.requests) == 1


def test_failed_task_status_fences_a_still_running_local_generator() -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=10)
    transport = FakeTransport([
        (200, document("workerLinks/worker-a", {"owner_uid": "owner-a", "revoked_at": None})),
        (200, document("users/owner-a/generationRequests/task-a", {
            "task_id": "task-a",
            "book_id": "book-a",
            "status": "FAILED_RETRYABLE",
            "attempt_id": 2,
            "deletion_generation": 0,
            "lease_owner": "worker-a",
            "lease_token": "still-matching-token",
            "lease_deadline": deadline,
        })),
    ])
    tasks = FirestoreWorkerTasks(make_client(transport))
    job = ChunkJob(
        "task-a",
        "book-a",
        "chunk-a",
        "chapter-a",
        publication_mode=PublicationMode.LOCAL_ONLY,
        voice_version="voice-a",
        attempt_id=2,
        lease_token="still-matching-token",
        deletion_generation=0,
        segments=(SegmentJob("segment-a", "chapter-a", 0, "测试", "a" * 64),),
    )

    with pytest.raises(GenerationError) as captured:
        tasks.assert_current(job)

    assert getattr(captured.value, "code", "") == "STALE_LEASE"
