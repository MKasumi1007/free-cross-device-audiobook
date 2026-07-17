from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum

from .errors import BookParseError
from .models import ParsedBook, TextSegment, assert_publication_allowed


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    GENERATING = "GENERATING"
    ENCODING = "ENCODING"
    UPLOADING = "UPLOADING"
    READY = "READY"
    PAUSED = "PAUSED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    DELETING = "DELETING"
    DELETED = "DELETED"
    CANCELLED = "CANCELLED"


class PauseReason(StrEnum):
    WAITING_FOR_MAC = "WAITING_FOR_MAC"
    WAITING_FOR_AC_POWER = "WAITING_FOR_AC_POWER"
    MEMORY_PRESSURE = "MEMORY_PRESSURE"
    OFFLINE = "OFFLINE"
    FREE_QUOTA = "FREE_QUOTA"
    RIGHTS_NOT_CONFIRMED = "RIGHTS_NOT_CONFIRMED"
    USER_PAUSED = "USER_PAUSED"
    VOICE_VERSION_CHANGED = "VOICE_VERSION_CHANGED"
    GITHUB_LIMITED = "GITHUB_LIMITED"


class GenerationPriority(IntEnum):
    BACKGROUND = 100
    ACTIVE_LOW_BUFFER = 200
    OPEN_WAITING_FIRST = 300


@dataclass(frozen=True)
class PlannedChunk:
    chunk_id: str
    order: int
    start_segment_id: str
    end_segment_id: str
    segment_ids: tuple[str, ...]
    estimated_seconds: float


@dataclass(frozen=True)
class GenerationBatch:
    book_id: str
    target_seconds: float
    estimated_seconds: float
    chunks: tuple[PlannedChunk, ...]


@dataclass(frozen=True)
class TaskLease:
    task_id: str
    status: TaskStatus
    attempt_id: int = 0
    lease_owner: str = ""
    lease_token: str = ""
    lease_deadline: datetime | None = None
    checkpoint_segment_id: str = ""

    def claim(self, owner: str, now: datetime, lease_seconds: int = 120) -> TaskLease:
        if self.status not in {TaskStatus.QUEUED, TaskStatus.FAILED_RETRYABLE}:
            raise BookParseError("TASK_NOT_CLAIMABLE", "当前任务状态不能领取。")
        return replace(
            self,
            status=TaskStatus.LEASED,
            attempt_id=self.attempt_id + 1,
            lease_owner=owner,
            lease_token=secrets.token_urlsafe(24),
            lease_deadline=now + timedelta(seconds=lease_seconds),
        )

    def checkpoint(self, token: str, segment_id: str, now: datetime) -> TaskLease:
        if token != self.lease_token or not self.lease_deadline or now >= self.lease_deadline:
            raise BookParseError("STALE_LEASE", "任务租约已失效，迟到结果不会覆盖新任务。")
        return replace(self, status=TaskStatus.GENERATING, checkpoint_segment_id=segment_id)


def estimate_segment_seconds(segment: TextSegment, chars_per_second: float = 4.2) -> float:
    if chars_per_second <= 0:
        raise ValueError("chars_per_second must be positive")
    readable_chars = len("".join(segment.spoken_text.split()))
    return readable_chars / chars_per_second


def _readable_segments(book: ParsedBook) -> list[TextSegment]:
    return [segment for chapter in book.chapters for segment in chapter.segments if segment.spoken_text]


def plan_generation_batch(
    book: ParsedBook,
    *,
    start_segment_id: str | None = None,
    target_seconds: float = 18_000,
    chunk_seconds: float = 600,
    chars_per_second: float = 4.2,
) -> GenerationBatch:
    assert_publication_allowed(book)
    segments = _readable_segments(book)
    if start_segment_id:
        try:
            start = next(index for index, segment in enumerate(segments) if segment.segment_id == start_segment_id)
        except StopIteration as exc:
            raise BookParseError("BAD_CURSOR", "保存的生成位置不在这本书中。") from exc
        segments = segments[start:]

    chunks: list[PlannedChunk] = []
    current: list[TextSegment] = []
    current_seconds = 0.0
    batch_seconds = 0.0

    def flush() -> None:
        nonlocal current, current_seconds
        if not current:
            return
        chunks.append(
            PlannedChunk(
                chunk_id=f"chunk-{len(chunks):05d}-{current[0].segment_id[:8]}",
                order=len(chunks),
                start_segment_id=current[0].segment_id,
                end_segment_id=current[-1].segment_id,
                segment_ids=tuple(segment.segment_id for segment in current),
                estimated_seconds=current_seconds,
            )
        )
        current = []
        current_seconds = 0.0

    for segment in segments:
        duration = estimate_segment_seconds(segment, chars_per_second)
        if batch_seconds >= target_seconds and current:
            break
        if current and current[-1].chapter_id != segment.chapter_id:
            flush()
            if batch_seconds >= target_seconds:
                break
        if current and current_seconds + duration > chunk_seconds:
            flush()
            if batch_seconds >= target_seconds:
                break
        current.append(segment)
        current_seconds += duration
        batch_seconds += duration
    flush()

    return GenerationBatch(
        book_id=book.book_id,
        target_seconds=target_seconds,
        estimated_seconds=batch_seconds,
        chunks=tuple(chunks),
    )


def should_auto_replenish(last_listened_at: datetime, playable_seconds: float, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if last_listened_at.tzinfo is None:
        last_listened_at = last_listened_at.replace(tzinfo=UTC)
    return now - last_listened_at <= timedelta(hours=48) and playable_seconds < 3600
