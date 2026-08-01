from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote

from audiobook_core.models import ParsedBook, TextSegment
from audiobook_core.planning import estimate_segment_seconds

from .generation import ChunkJob, PublishedAsset, PublishedChunk


LOCAL_AGENT_URL = "http://127.0.0.1:17832"
AUTO_RESUME_PAUSES = frozenset({"WAITING_FOR_AC_POWER", "MEMORY_PRESSURE", "WAITING_FOR_MAC"})
ACTIVE_STATUSES = frozenset({"GENERATING", "ENCODING", "UPLOADING"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_task_id(value: str) -> bool:
    return bool(value) and Path(value).name == value and value not in {".", ".."}


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class LocalGenerationStore:
    """Persistent Mac-only queue and audio index used when cloud quota is unavailable."""

    SCHEMA_VERSION = 1

    def __init__(self, root: Path) -> None:
        self.root = root
        self.queue_path = root / "queue.json"
        self.assets_root = root / "assets"
        self._lock = threading.RLock()

    def enqueue(
        self,
        owner_uid: str,
        book: ParsedBook,
        chapter_ids: list[str],
        voice_version: str,
        task_ids: list[str] | None = None,
    ) -> dict[str, int]:
        selected = set(chapter_ids)
        allowed_tasks = set(task_ids) if task_ids else None
        planned = self._plan(book, selected, voice_version, allowed_tasks)
        if not planned:
            raise ValueError("所选章节没有可朗读的正文。")
        with self._lock:
            payload = self._read()
            tasks = payload["tasks"]
            existing = {str(task.get("task_id")): task for task in tasks}
            active_priorities = [
                int(task.get("priority") or 0)
                for task in tasks
                if task.get("status") not in {"READY", "CANCELLED"}
            ]
            append_priority = min(active_priorities) - 100 if active_priorities else 10_000
            created = 0
            resumed = 0
            unchanged = 0
            for index, plan in enumerate(planned):
                task_id = str(plan["task_id"])
                previous = existing.get(task_id)
                priority = append_priority - index
                if previous is None:
                    task = {
                        **plan,
                        "owner_uid": owner_uid,
                        "voice_version": voice_version,
                        "status": "QUEUED",
                        "priority": priority,
                        "attempt_id": 0,
                        "deletion_generation": 0,
                        "pause_reason": "",
                        "requested_action": "",
                        "progress_stage": "QUEUED",
                        "progress_completed_units": 0,
                        "progress_total_units": 0,
                        "progress_completed_segments": 0,
                        "progress_total_segments": 0,
                        "progress_current_segment_id": "",
                        "progress_current_segment_order": 0,
                        "progress_current_piece": 0,
                        "progress_current_piece_total": 0,
                        "progress_generated_audio_seconds": 0.0,
                        "progress_elapsed_seconds": 0.0,
                        "progress_eta_seconds": None,
                        "sync_status": "PENDING",
                        "sync_error": "",
                        "created_at": _now(),
                        "updated_at": _now(),
                    }
                    tasks.append(task)
                    existing[task_id] = task
                    created += 1
                    continue
                if previous.get("status") in {
                    "PAUSED", "FAILED_RETRYABLE", "FAILED_FINAL", "CANCELLED"
                }:
                    previous.update({
                        "owner_uid": owner_uid,
                        "voice_version": voice_version,
                        "status": "QUEUED",
                        "priority": priority,
                        "pause_reason": "",
                        "requested_action": "",
                        "retry_not_before": None,
                        "sync_error": "",
                        "updated_at": _now(),
                    })
                    resumed += 1
                    continue
                unchanged += 1
            self._write(payload)
        return {
            "chapters": len({str(item["chapter_id"]) for item in planned}),
            "created": created,
            "resumed": resumed,
            "unchanged": unchanged,
        }

    def next_task(self) -> dict[str, Any] | None:
        with self._lock:
            payload = self._read()
            tasks = payload["tasks"]
            now = datetime.now(UTC)
            candidates = []
            recovered = False
            for task in tasks:
                status = str(task.get("status") or "")
                if (
                    status == "READY"
                    and task.get("sync_status") != "SYNCED"
                    and not self._ready_assets_exist(task)
                ):
                    task.update({
                        "status": "FAILED_RETRYABLE",
                        "retry_not_before": None,
                        "progress_stage": "QUEUED",
                        "error_code": "LOCAL_ASSET_MISSING",
                        "sync_status": "PENDING",
                        "sync_error": "",
                        "updated_at": _now(),
                    })
                    status = "FAILED_RETRYABLE"
                    recovered = True
                if status in ACTIVE_STATUSES:
                    task.update({
                        "status": "FAILED_RETRYABLE",
                        "retry_not_before": None,
                        "progress_stage": "QUEUED",
                        "error_code": "LOCAL_AGENT_RESTARTED",
                        "updated_at": _now(),
                    })
                    status = "FAILED_RETRYABLE"
                    recovered = True
                if status == "PAUSED" and task.get("pause_reason") in AUTO_RESUME_PAUSES:
                    pass
                elif status not in {"QUEUED", "FAILED_RETRYABLE"}:
                    continue
                retry_at = task.get("retry_not_before")
                if retry_at:
                    try:
                        if datetime.fromisoformat(str(retry_at)) > now:
                            continue
                    except ValueError:
                        pass
                candidates.append(task)
            if recovered:
                self._write(payload)
            candidates.sort(key=lambda item: (-int(item.get("priority") or 0), str(item["task_id"])))
            return dict(candidates[0]) if candidates else None

    def claim(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._read()
            task = self._find(payload, task_id)
            if task is None:
                raise ValueError("本地生成任务不存在。")
            task.update({
                "status": "GENERATING",
                "attempt_id": int(task.get("attempt_id") or 0) + 1,
                "lease_token": secrets.token_urlsafe(24),
                "pause_reason": "",
                "retry_not_before": None,
                "progress_stage": "MODEL_LOADING",
                "progress_started_at": _now(),
                "updated_at": _now(),
            })
            self._write(payload)
            return dict(task)

    def assert_current(self, task_id: str, attempt_id: int, lease_token: str) -> None:
        with self._lock:
            task = self._find(self._read(), task_id)
            if (
                task is None
                or task.get("status") not in ACTIVE_STATUSES
                or int(task.get("attempt_id") or 0) != attempt_id
                or not secrets.compare_digest(str(task.get("lease_token") or ""), lease_token)
            ):
                raise RuntimeError("本地生成任务已经暂停或被替换。")

    def state(self, task_id: str, status: str) -> None:
        self.update(task_id, status=status, progress_stage=status)

    def update(self, task_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            payload = self._read()
            task = self._find(payload, task_id)
            if task is None:
                raise ValueError("本地生成任务不存在。")
            task.update(changes)
            task["updated_at"] = _now()
            self._write(payload)
            return dict(task)

    def progress(self, task_id: str, **changes: Any) -> None:
        self.update(task_id, progress_stage="GENERATING", **changes)

    def fail(self, task_id: str, code: str, message: str, retry_at: datetime) -> None:
        self.update(
            task_id,
            status="FAILED_RETRYABLE",
            progress_stage="FAILED_RETRYABLE",
            error_code=code,
            error_message=message,
            retry_not_before=retry_at.isoformat(),
        )

    def pause_for_resource(self, task_id: str, reason: str) -> None:
        self.update(task_id, status="PAUSED", pause_reason=reason, progress_stage=reason)

    def record_ready(self, task_id: str, job: ChunkJob, published: PublishedChunk) -> None:
        self.update(
            task_id,
            status="READY",
            progress_stage="READY",
            progress_eta_seconds=0.0,
            chunk_id=job.chunk_id,
            start_segment_id=job.segments[0].segment_id,
            end_segment_id=job.segments[-1].segment_id,
            duration_seconds=published.duration_seconds,
            asset_id=published.audio.asset_id,
            asset_name=published.audio.name,
            asset_url=published.audio.url,
            sha256=published.audio.sha256,
            byte_size=published.audio.byte_size,
            timeline_asset_id=published.timeline.asset_id,
            timeline_name=published.timeline.name,
            timeline_url=published.timeline.url,
            timeline_sha256=published.timeline.sha256,
            sync_status="PENDING",
            sync_error="",
            completed_at=_now(),
        )

    def act(self, task_ids: list[str], action: str) -> int:
        if action not in {"PAUSE", "RESUME", "REMOVE"}:
            raise ValueError("不支持的本地队列操作。")
        changed = 0
        with self._lock:
            payload = self._read()
            for task_id in task_ids:
                task = self._find(payload, task_id)
                if task is None:
                    continue
                status = str(task.get("status") or "")
                if action == "PAUSE":
                    if status in ACTIVE_STATUSES:
                        task["requested_action"] = "PAUSE"
                    elif status in {"QUEUED", "FAILED_RETRYABLE"}:
                        task.update({"status": "PAUSED", "pause_reason": "USER_PAUSED"})
                    else:
                        continue
                elif action == "RESUME":
                    if status not in {"PAUSED", "FAILED_RETRYABLE", "FAILED_FINAL", "CANCELLED"}:
                        continue
                    task.update({
                        "status": "QUEUED",
                        "pause_reason": "",
                        "requested_action": "",
                        "retry_not_before": None,
                    })
                else:
                    if status in ACTIVE_STATUSES:
                        task["requested_action"] = "REMOVE"
                    elif status not in {"READY", "CANCELLED"}:
                        task.update({"status": "CANCELLED", "requested_action": ""})
                    else:
                        continue
                task["updated_at"] = _now()
                changed += 1
            if changed:
                self._write(payload)
        return changed

    def reorder(self, task_ids: list[str]) -> int:
        with self._lock:
            payload = self._read()
            base = 10_000
            changed = 0
            for index, task_id in enumerate(task_ids):
                task = self._find(payload, task_id)
                if task is None or task.get("status") in {"READY", "CANCELLED"}:
                    continue
                task["priority"] = base - index
                task["updated_at"] = _now()
                changed += 1
            if changed:
                self._write(payload)
            return changed

    def ready_for_sync(self, owner_uid: str) -> dict[str, Any] | None:
        with self._lock:
            tasks = [
                item for item in self._read()["tasks"]
                if item.get("status") == "READY"
                and item.get("sync_status") in {"PENDING", "FAILED"}
                and item.get("owner_uid") == owner_uid
            ]
            tasks.sort(key=lambda item: str(item.get("completed_at") or ""))
            return dict(tasks[0]) if tasks else None

    def mark_syncing(self, task_id: str) -> None:
        self.update(task_id, sync_status="SYNCING", sync_error="")

    def mark_sync_pending(self, task_id: str, message: str) -> None:
        self.update(task_id, sync_status="FAILED", sync_error=message[:240])

    def mark_synced(self, task_id: str) -> None:
        self.update(task_id, sync_status="SYNCED", sync_error="", synced_at=_now())

    def status(self) -> dict[str, Any]:
        with self._lock:
            tasks = [self._public_task(item) for item in self._read()["tasks"]]
        chunks = [
            self._audio_chunk(item)
            for item in tasks
            if item.get("status") == "READY"
            and self.asset_path(str(item.get("task_id") or ""), "audio") is not None
            and self.asset_path(str(item.get("task_id") or ""), "timeline") is not None
        ]
        return {
            "schema_version": self.SCHEMA_VERSION,
            "tasks": tasks,
            "audio_chunks": chunks,
            "pending_sync": sum(
                1 for item in tasks
                if item.get("status") == "READY" and item.get("sync_status") != "SYNCED"
            ),
        }

    def asset_path(self, task_id: str, kind: str) -> Path | None:
        if not _safe_task_id(task_id) or kind not in {"audio", "timeline"}:
            return None
        with self._lock:
            task = self._find(self._read(), task_id)
            if task is None or task.get("status") != "READY":
                return None
            name = str(task.get("asset_name" if kind == "audio" else "timeline_name") or "")
        candidate = (self.assets_root / task_id / name).resolve()
        parent = (self.assets_root / task_id).resolve()
        if not name or candidate.parent != parent or not candidate.is_file():
            return None
        return candidate

    def _ready_assets_exist(self, task: dict[str, Any]) -> bool:
        task_id = str(task.get("task_id") or "")
        if not _safe_task_id(task_id):
            return False
        parent = (self.assets_root / task_id).resolve()
        for field in ("asset_name", "timeline_name"):
            name = str(task.get(field) or "")
            candidate = (parent / name).resolve()
            if not name or candidate.parent != parent or not candidate.is_file():
                return False
        return True

    def task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._find(self._read(), task_id)
            return dict(task) if task else None

    def _plan(
        self,
        book: ParsedBook,
        selected: set[str],
        voice_version: str,
        allowed_tasks: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        planned: list[dict[str, Any]] = []
        for chapter in book.chapters:
            if chapter.chapter_id not in selected:
                continue
            current: list[TextSegment] = []
            current_seconds = 0.0
            chunk_order = 0

            def flush() -> None:
                nonlocal current, current_seconds, chunk_order
                if not current:
                    return
                start = current[0]
                task_id = f"chunk-{book.book_id}-{voice_version}-{start.segment_id}"
                plan = {
                    "task_id": task_id,
                    "book_id": book.book_id,
                    "book_title": book.title,
                    "chapter_id": chapter.chapter_id,
                    "chapter_title": chapter.title,
                    "chunk_order": chunk_order,
                    "estimated_seconds": current_seconds,
                    "start_segment_id": start.segment_id,
                    "target_seconds": 600.0,
                    "execution_mode": "LOCAL",
                    "storage_mode": "LOCAL_MAC",
                }
                if allowed_tasks is None or task_id in allowed_tasks:
                    planned.append(plan)
                current = []
                current_seconds = 0.0
                chunk_order += 1

            for segment in chapter.segments:
                if not segment.spoken_text:
                    continue
                duration = estimate_segment_seconds(segment)
                if current and current_seconds + duration > 600:
                    flush()
                current.append(segment)
                current_seconds += duration
            flush()
        return planned

    def _read(self) -> dict[str, Any]:
        if not self.queue_path.is_file():
            return {"schema_version": self.SCHEMA_VERSION, "tasks": []}
        try:
            value = json.loads(self.queue_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {"schema_version": self.SCHEMA_VERSION, "tasks": []}
        tasks = value.get("tasks") if isinstance(value, dict) else None
        return {
            "schema_version": self.SCHEMA_VERSION,
            "tasks": tasks if isinstance(tasks, list) else [],
        }

    def _write(self, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        temporary = self.queue_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, self.queue_path)

    @staticmethod
    def _find(payload: dict[str, Any], task_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in payload["tasks"] if item.get("task_id") == task_id),
            None,
        )

    @staticmethod
    def _public_task(task: dict[str, Any]) -> dict[str, Any]:
        hidden = {"lease_token", "error_message"}
        return {name: value for name, value in task.items() if name not in hidden}

    @staticmethod
    def _audio_chunk(task: dict[str, Any]) -> dict[str, Any]:
        fields = {
            "owner_uid", "task_id", "book_id", "chunk_id", "chapter_id",
            "status", "start_segment_id", "end_segment_id", "duration_seconds",
            "asset_id", "asset_url", "sha256", "byte_size", "timeline_asset_id",
            "timeline_url", "timeline_sha256", "voice_version", "deletion_generation",
            "sync_status", "completed_at", "execution_mode",
        }
        result = {name: task.get(name) for name in fields}
        result["storage_mode"] = "LOCAL_MAC"
        return result


class LocalAssetPublisher:
    storage_mode = "LOCAL_MAC"

    def __init__(self, root: Path, task_id: str) -> None:
        if not _safe_task_id(task_id):
            raise ValueError("本地任务标识无效。")
        self.root = root
        self.task_id = task_id
        self._created: dict[int, Path] = {}

    def publish(self, _book_id: str, path: Path, asset_name: str) -> PublishedAsset:
        destination_dir = self.root / self.task_id
        destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination_dir.chmod(0o700)
        destination = destination_dir / Path(asset_name).name
        digest = _file_hash(path)
        if destination.is_file() and _file_hash(destination) == digest:
            created = False
        else:
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copyfile(path, temporary)
            temporary.chmod(0o600)
            os.replace(temporary, destination)
            created = True
        asset_id = int(digest[:15], 16)
        self._created[asset_id] = destination
        kind = "audio" if destination.suffix == ".m4a" else "timeline"
        url = (
            f"{LOCAL_AGENT_URL}/v1/local-generation/assets/"
            f"{quote(self.task_id, safe='')}/{kind}"
        )
        return PublishedAsset(
            asset_id=asset_id,
            name=destination.name,
            url=url,
            byte_size=destination.stat().st_size,
            sha256=digest,
            created=created,
            storage_mode=self.storage_mode,
            content_type="audio/mp4" if kind == "audio" else "application/gzip",
        )

    def delete(self, _book_id: str, asset_id: int) -> None:
        path = self._created.pop(asset_id, None)
        if path and path.is_file():
            path.unlink()


class LocalFence:
    def __init__(self, store: LocalGenerationStore, task: dict[str, Any]) -> None:
        self.store = store
        self.task_id = str(task["task_id"])
        self.attempt_id = int(task["attempt_id"])
        self.lease_token = str(task["lease_token"])
        self.started = datetime.now(UTC)

    def assert_current(self, _job: ChunkJob) -> None:
        self.store.assert_current(self.task_id, self.attempt_id, self.lease_token)

    def state(self, status: str) -> None:
        self.store.state(self.task_id, status)

    def progress(
        self,
        *,
        completed_units: int,
        total_units: int,
        completed_segments: int,
        total_segments: int,
        current_segment_id: str,
        current_segment_order: int,
        current_piece: int,
        current_piece_total: int,
        generated_audio_seconds: float,
    ) -> None:
        elapsed = max(0.0, (datetime.now(UTC) - self.started).total_seconds())
        remaining = max(0, total_units - completed_units)
        eta = elapsed / completed_units * remaining if completed_units and remaining else 0.0
        self.store.progress(
            self.task_id,
            progress_completed_units=completed_units,
            progress_total_units=total_units,
            progress_completed_segments=completed_segments,
            progress_total_segments=total_segments,
            progress_current_segment_id=current_segment_id,
            progress_current_segment_order=current_segment_order,
            progress_current_piece=current_piece,
            progress_current_piece_total=current_piece_total,
            progress_generated_audio_seconds=round(generated_audio_seconds, 3),
            progress_elapsed_seconds=round(elapsed, 1),
            progress_eta_seconds=round(eta, 1),
        )
