from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from audiobook_core.errors import BookParseError
from audiobook_core.parser import parse_book
from audiobook_core.planning import TaskLease, TaskStatus, plan_generation_batch, should_auto_replenish


def make_long_book(path: Path, *, rights_confirmed: bool):
    paragraphs = [f"第{i}段。" + "这是用于估算朗读时间的项目自制文字。" * 3 for i in range(80)]
    path.write_text("\n\n".join(paragraphs), encoding="utf-8")
    return parse_book(path, rights_confirmed=rights_confirmed)


def test_five_hour_batch_is_split_near_ten_minute_boundaries(tmp_path: Path) -> None:
    book = make_long_book(tmp_path / "long.txt", rights_confirmed=True)
    batch = plan_generation_batch(
        book,
        target_seconds=600,
        chunk_seconds=120,
        chars_per_second=4,
    )
    assert len(batch.chunks) >= 4
    assert 600 <= batch.estimated_seconds < 640
    assert all(chunk.estimated_seconds <= 140 for chunk in batch.chunks)
    flattened = [segment_id for chunk in batch.chunks for segment_id in chunk.segment_ids]
    assert len(flattened) == len(set(flattened))


def test_local_only_book_cannot_create_public_generation_batch(tmp_path: Path) -> None:
    book = make_long_book(tmp_path / "private.txt", rights_confirmed=False)
    with pytest.raises(BookParseError) as error:
        plan_generation_batch(book)
    assert error.value.code == "RIGHTS_NOT_CONFIRMED"


def test_lease_token_fences_late_worker() -> None:
    now = datetime.now(UTC)
    claimed = TaskLease(task_id="task-1", status=TaskStatus.QUEUED).claim("worker-a", now, 60)
    updated = claimed.checkpoint(claimed.lease_token, "segment-1", now + timedelta(seconds=10))
    assert updated.checkpoint_segment_id == "segment-1"
    with pytest.raises(BookParseError) as error:
        claimed.checkpoint("wrong-token", "segment-2", now + timedelta(seconds=20))
    assert error.value.code == "STALE_LEASE"


def test_inactive_book_does_not_auto_replenish() -> None:
    now = datetime.now(UTC)
    assert should_auto_replenish(now - timedelta(hours=1), 3599, now)
    assert not should_auto_replenish(now - timedelta(hours=49), 0, now)
    assert not should_auto_replenish(now - timedelta(hours=1), 3600, now)
