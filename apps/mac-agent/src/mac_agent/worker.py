from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from audiobook_core.models import ParsedBook
from audiobook_core.models import PublicationMode
from audiobook_core.planning import plan_generation_batch

from .book_assets import BookTextPublisher
from .cleanup import clean_expired_generation_files
from .generation import (
    ChunkJob,
    ChunkPipeline,
    GenerationError,
    PublishedChunk,
    SegmentGenerator,
    SegmentJob,
)
from .error_reporting import reporter
from .firebase_rest import FirebaseRestError
from .library import LocalLibrary
from .local_generation import LocalAssetPublisher, LocalFence, LocalGenerationStore
from .private_assets import FirestorePrivateAssetPublisher
from .repository_assets import GitHubAudiobookPublisher, GitHubRepositoryAssetPublisher
from .reconciliation import AudioReconciler
from .resources import ResourcePolicy
from .task_cloud import CloudBookDeletion, CloudDeletion, CloudTask, FirestoreWorkerTasks
from .voice import VoiceProfile, VoiceRegistry


GeneratorFactory = Callable[[VoiceProfile], SegmentGenerator]
ACTIVE_LEASE_STATES = frozenset({"LEASED", "GENERATING", "ENCODING", "UPLOADING"})


class RenewingFence:
    def __init__(self, tasks: FirestoreWorkerTasks, task: CloudTask) -> None:
        self.tasks = tasks
        self.task = task
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None
        self._started_monotonic = time.monotonic()

    def start(self) -> None:
        self._heartbeat = threading.Thread(target=self._run_heartbeat, daemon=True)
        self._heartbeat.start()

    def stop(self) -> None:
        self._stop.set()
        if self._heartbeat:
            self._heartbeat.join(timeout=2)

    def assert_current(self, job: ChunkJob) -> None:
        with self._lock:
            current = self.tasks.get(self.task.owner_uid, self.task.task_id)
            if (
                current is None
                or current.status not in ACTIVE_LEASE_STATES
                or current.attempt_id != job.attempt_id
                or current.lease_token != job.lease_token
                or current.deletion_generation != job.deletion_generation
                or current.lease_deadline is None
                or current.lease_deadline <= datetime.now(UTC)
            ):
                raise GenerationError("STALE_LEASE", "任务租约已失效，迟到结果不会发布。")
            self.task = current
            if current.lease_deadline <= datetime.now(UTC) + timedelta(minutes=5):
                self.task = self.tasks.renew(current)

    def state(self, status: str) -> None:
        with self._lock:
            current = self.tasks.get(self.task.owner_uid, self.task.task_id)
            if current is None or current.status not in ACTIVE_LEASE_STATES:
                raise GenerationError("STALE_LEASE", "生成任务已暂停、撤销或被其他尝试接管。")
            changes: dict[str, object] = {"progress_stage": status}
            if status == "READY":
                changes["progress_eta_seconds"] = 0.0
            self.task = self.tasks.transition(current, status, **changes)

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
        with self._lock:
            current = self.tasks.get(self.task.owner_uid, self.task.task_id)
            if current is None or current.status not in ACTIVE_LEASE_STATES:
                raise GenerationError("STALE_LEASE", "生成任务已暂停、撤销或被其他尝试接管。")
            elapsed = max(0.0, time.monotonic() - self._started_monotonic)
            remaining_units = max(0, total_units - completed_units)
            eta = (
                elapsed / completed_units * remaining_units
                if completed_units > 0 and remaining_units > 0
                else 0.0 if completed_units >= total_units and total_units > 0 else None
            )
            self.task = self.tasks.transition(
                current,
                current.status,
                progress_stage="GENERATING",
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
                progress_eta_seconds=round(eta, 1) if eta is not None else None,
                checkpoint_segment_id=current_segment_id,
                checkpoint_order=current_segment_order,
            )

    def _run_heartbeat(self) -> None:
        while not self._stop.wait(5 * 60):
            with self._lock:
                current = self.tasks.get(self.task.owner_uid, self.task.task_id)
                if (
                    current is None
                    or current.status not in ACTIVE_LEASE_STATES
                    or current.lease_token != self.task.lease_token
                ):
                    return
                self.task = self.tasks.renew(current)


class MacGenerationWorker:
    MAX_READY_AUDIO_SECONDS = 5 * 60 * 60
    CAPACITY_CHECK_SECONDS = 5 * 60

    def __init__(
        self,
        *,
        tasks: FirestoreWorkerTasks,
        library: LocalLibrary,
        voices: VoiceRegistry,
        policy: ResourcePolicy,
        generator_factory: GeneratorFactory,
        work_root: Path,
        repository: str,
        local_tasks: LocalGenerationStore | None = None,
    ) -> None:
        self.tasks = tasks
        self.library = library
        self.voices = voices
        self.policy = policy
        self.generator_factory = generator_factory
        self.work_root = work_root
        self.repository = repository
        self.local_tasks = local_tasks
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._generator: SegmentGenerator | None = None
        self._voice_version = ""
        self._published_books: set[str] = set()
        self.last_state = "IDLE"
        self.last_error = ""
        self._last_presence = 0.0
        self._last_cleanup = float("-inf")
        self._last_retention = float("-inf")
        self._last_reconciliation = float("-inf")
        self._ready_audio_seconds: float | None = None
        self._last_capacity_check = float("-inf")
        self._cloud_failure_count = 0
        self._cloud_backoff_until = 0.0
        self._cloud_backoff_seconds = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="audiobook-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._unload_generator()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
                if (
                    self._cloud_failure_count
                    and self.last_state != "FREE_QUOTA_LOCAL_READY"
                    and not self.last_state.startswith("LOCAL_")
                ):
                    self._clear_cloud_backoff()
                self.last_error = ""
            except Exception as error:
                worked = False
                if self._is_quota_error(error):
                    self._activate_cloud_backoff()
                    self.last_state = "FREE_QUOTA_LOCAL_READY"
                    self.last_error = "免费云同步额度已暂停，本地生成仍可继续。"
                else:
                    self.last_state = "ERROR"
                    self.last_error = "后台生成暂时中断，完整原因已写入本机日志。"
                reporter.record(
                    "worker.run_forever",
                    error,
                    code=getattr(error, "code", "BACKGROUND_WORKER_FAILED"),
                    details={"last_state": self.last_state},
                )
            delay = self._next_delay(worked)
            self._wake.wait(delay)
            self._wake.clear()

    def run_once(self) -> bool:
        self._periodic_cleanup()
        if self.local_tasks:
            local_task = self.local_tasks.next_task()
            if local_task is not None:
                return self._process_local_task(local_task)
        if time.monotonic() < self._cloud_backoff_until:
            self.last_state = "FREE_QUOTA_LOCAL_READY"
            return False
        owner_uid = self.tasks.active_owner()
        if owner_uid is None:
            self.last_state = "WAITING_FOR_PAIRING"
            return False
        if time.monotonic() - self._last_presence >= 4 * 60:
            self.tasks.touch_presence()
            self._last_presence = time.monotonic()
        self._periodic_retention(owner_uid)
        deletion = self.tasks.next_deletion(owner_uid)
        if deletion is not None:
            return self._process_deletion(deletion)
        book_deletion = self.tasks.next_book_deletion(owner_uid)
        if book_deletion is not None:
            return self._process_book_deletion(book_deletion)
        if self.policy.pause_reason() == "USER_PAUSED":
            self._unload_generator()
            self.last_state = "USER_PAUSED"
            return False
        task = self.tasks.next_task(owner_uid)
        if task is None:
            self._unload_generator()
            if self._sync_private_book_texts(owner_uid):
                return True
            return self._maybe_reconcile(owner_uid)
        if self._audio_cache_is_full(owner_uid):
            self._unload_generator()
            self.last_state = "WAITING_FOR_CACHE_SPACE"
            return False
        profile = self.voices.load()
        if profile is None or not profile.confirmed:
            self._unload_generator()
            self.last_state = "WAITING_FOR_VOICE"
            return False
        if task.status == "PAUSED":
            pause_reason = self.policy.pause_reason()
            if task.pause_reason in {"WAITING_FOR_AC_POWER", "MEMORY_PRESSURE"} and pause_reason:
                self.last_state = pause_reason
                return False
            if task.pause_reason == "WAITING_FOR_MAC" and self.library.get(task.book_id) is None:
                self.last_state = "WAITING_FOR_MAC"
                return False
        task = self.tasks.claim(task)
        pause_reason = self.policy.pause_reason()
        if pause_reason:
            self._unload_generator()
            self.tasks.transition(task, "PAUSED", pause_reason=pause_reason)
            self.last_state = pause_reason
            return True
        if task.voice_version and task.voice_version != profile.voice_version:
            self.tasks.transition(task, "PAUSED", pause_reason="VOICE_VERSION_CHANGED")
            self.last_state = "VOICE_VERSION_CHANGED"
            return True
        book = self.library.get(task.book_id)
        if book is None:
            self.tasks.transition(task, "PAUSED", pause_reason="WAITING_FOR_MAC")
            self.last_state = "WAITING_FOR_MAC"
            return True
        expected_storage = (
            "PRIVATE_FIRESTORE"
            if book.publication_mode is PublicationMode.LOCAL_ONLY
            else "PUBLIC_GITHUB"
        )
        if task.storage_mode != expected_storage:
            self.tasks.transition(
                task,
                "FAILED_RETRYABLE",
                error_code="STORAGE_MODE_MISMATCH",
                error_message="书籍权利设置与任务存储方式不一致，已停止发布。",
            )
            self.last_state = "FAILED_RETRYABLE"
            return True
        task = self.tasks.transition(task, "GENERATING", progress_stage="MODEL_LOADING")
        self.last_state = "MODEL_LOADING"
        fence = RenewingFence(self.tasks, task)
        fence.start()
        try:
            self._ensure_book_text(fence.task.owner_uid, fence.task.task_id, book)
            if self._publish_local_ready(fence.task, book, profile, fence):
                self.last_state = "LOCAL_SYNCED"
                return True
            job = self._make_job(book, task, profile)
            generator = self._generator_for(profile)
            self.last_state = "GENERATING"
            publisher = (
                FirestorePrivateAssetPublisher(
                    self.tasks.client,
                    task.owner_uid,
                    task_id=task.task_id,
                )
                if book.publication_mode is PublicationMode.LOCAL_ONLY
                else GitHubAudiobookPublisher(self.repository)
            )
            pipeline = ChunkPipeline(
                self.work_root,
                generator,
                publisher,
                fence,
                observer=fence,
            )
            published = pipeline.run(job)
            self.tasks.record_ready(fence.task, job, published)
            if self._ready_audio_seconds is not None:
                self._ready_audio_seconds += published.duration_seconds
            fence.state("READY")
            self.last_state = "READY"
            return True
        except GenerationError as error:
            reporter.record(
                "worker.generate_chunk",
                error,
                code=error.code,
                details={"task_id": task.task_id, "book_id": task.book_id},
            )
            current = self.tasks.get(task.owner_uid, task.task_id)
            if (
                current
                and current.status in ACTIVE_LEASE_STATES
                and current.lease_token == task.lease_token
            ):
                retry_at = datetime.now(UTC) + self._retry_delay(error.code, current.attempt_id)
                self.tasks.transition(
                    current,
                    "FAILED_RETRYABLE",
                    error_code=error.code,
                    error_message="生成暂时中断，稍后会从检查点继续。",
                    retry_not_before=retry_at,
                    progress_stage="FAILED_RETRYABLE",
                )
            self.last_state = "FAILED_RETRYABLE"
            self.last_error = str(error)
            return True
        finally:
            fence.stop()
            self._unload_generator()

    def _process_local_task(self, pending: dict[str, object]) -> bool:
        if self.local_tasks is None:
            return False
        task_id = str(pending["task_id"])
        pause_reason = self.policy.pause_reason()
        if pause_reason:
            self._unload_generator()
            self.local_tasks.pause_for_resource(task_id, pause_reason)
            self.last_state = pause_reason
            return False
        profile = self.voices.load()
        if profile is None or not profile.confirmed:
            self._unload_generator()
            self.last_state = "WAITING_FOR_VOICE"
            return False
        book = self.library.get(str(pending["book_id"]))
        if book is None:
            self.local_tasks.pause_for_resource(task_id, "WAITING_FOR_MAC")
            self.last_state = "WAITING_FOR_MAC"
            return False
        claimed = self.local_tasks.claim(task_id)
        if str(claimed.get("voice_version") or "") != profile.voice_version:
            self.local_tasks.pause_for_resource(task_id, "VOICE_VERSION_CHANGED")
            self.last_state = "VOICE_VERSION_CHANGED"
            return False
        task = CloudTask(
            owner_uid=str(claimed.get("owner_uid") or ""),
            task_id=task_id,
            book_id=book.book_id,
            status="GENERATING",
            priority=int(claimed.get("priority") or 0),
            attempt_id=int(claimed.get("attempt_id") or 0),
            deletion_generation=int(claimed.get("deletion_generation") or 0),
            start_segment_id=str(claimed.get("start_segment_id") or "") or None,
            target_seconds=float(claimed.get("target_seconds") or 600),
            voice_version=profile.voice_version,
            storage_mode="LOCAL_MAC",
            lease_token=str(claimed.get("lease_token") or ""),
        )
        fence = LocalFence(self.local_tasks, claimed)
        try:
            job = self._make_job(book, task, profile)
            generator = self._generator_for(profile)
            self.last_state = "LOCAL_GENERATING"
            publisher = LocalAssetPublisher(self.local_tasks.assets_root, task_id)
            pipeline = ChunkPipeline(
                self.work_root,
                generator,
                publisher,
                fence,
                observer=fence,
            )
            published = pipeline.run(job)
            self.local_tasks.record_ready(task_id, job, published)
            self.last_state = "LOCAL_READY_WAITING_SYNC"
            self.last_error = ""
            return True
        except GenerationError as error:
            reporter.record(
                "worker.generate_local_chunk",
                error,
                code=error.code,
                details={"task_id": task_id, "book_id": book.book_id},
            )
            retry_at = datetime.now(UTC) + self._retry_delay(
                error.code,
                int(claimed.get("attempt_id") or 1),
            )
            self.local_tasks.fail(
                task_id,
                error.code,
                "本地生成暂时中断，稍后会从检查点继续。",
                retry_at,
            )
            self.last_state = "LOCAL_FAILED_RETRYABLE"
            self.last_error = str(error)
            return True
        finally:
            self._unload_generator()

    def _publish_local_ready(
        self,
        task: CloudTask,
        book: ParsedBook,
        profile: VoiceProfile,
        fence: RenewingFence,
    ) -> bool:
        if self.local_tasks is None:
            return False
        local = self.local_tasks.task(task.task_id)
        if local is None or local.get("status") != "READY":
            return False
        audio_path = self.local_tasks.asset_path(task.task_id, "audio")
        timeline_path = self.local_tasks.asset_path(task.task_id, "timeline")
        if audio_path is None or timeline_path is None:
            self.local_tasks.mark_sync_pending(task.task_id, "本地音频文件不完整。")
            return False
        job = self._make_job(book, task, profile)
        self.local_tasks.mark_syncing(task.task_id)
        try:
            publisher = (
                FirestorePrivateAssetPublisher(
                    self.tasks.client,
                    task.owner_uid,
                    task_id=task.task_id,
                )
                if book.publication_mode is PublicationMode.LOCAL_ONLY
                else GitHubAudiobookPublisher(self.repository)
            )
            audio = publisher.publish(
                book.book_id,
                audio_path,
                str(local.get("asset_name") or audio_path.name),
            )
            timeline = publisher.publish(
                book.book_id,
                timeline_path,
                str(local.get("timeline_name") or timeline_path.name),
            )
            published = PublishedChunk(
                chunk_id=job.chunk_id,
                duration_seconds=float(local.get("duration_seconds") or 0),
                audio=audio,
                timeline=timeline,
                reused=True,
            )
            self.tasks.record_ready(fence.task, job, published)
            fence.state("READY")
            self.local_tasks.mark_synced(task.task_id)
            return True
        except Exception as error:
            self.local_tasks.mark_sync_pending(task.task_id, str(error))
            raise

    def enqueue_local(
        self,
        owner_uid: str,
        selections: list[dict[str, object]],
        voice_version: str,
    ) -> dict[str, int]:
        if self.local_tasks is None:
            raise RuntimeError("本地生成队列尚未启用。")
        totals = {"chapters": 0, "created": 0, "resumed": 0, "unchanged": 0}
        for selection in selections:
            book = self.library.get(str(selection.get("book_id") or ""))
            if book is None:
                raise ValueError("这本书尚未保存在当前 Mac，无法本地生成。")
            raw_chapter_ids = selection.get("chapter_ids")
            chapter_ids = (
                [str(value) for value in raw_chapter_ids]
                if isinstance(raw_chapter_ids, list)
                else []
            )
            raw_task_ids = selection.get("task_ids")
            task_ids = (
                [str(value) for value in raw_task_ids]
                if isinstance(raw_task_ids, list) and raw_task_ids
                else None
            )
            result = self.local_tasks.enqueue(
                owner_uid,
                book,
                chapter_ids,
                voice_version,
                task_ids,
            )
            for name in totals:
                totals[name] += int(result[name])
        self._wake.set()
        return totals

    def local_status(self) -> dict[str, object]:
        status = self.local_tasks.status() if self.local_tasks else {
            "schema_version": 1,
            "tasks": [],
            "audio_chunks": [],
            "pending_sync": 0,
        }
        status["worker"] = self.status()
        return status

    def local_action(self, task_ids: list[str], action: str) -> int:
        if self.local_tasks is None:
            return 0
        changed = self.local_tasks.act(task_ids, action)
        self._wake.set()
        return changed

    def local_reorder(self, task_ids: list[str]) -> int:
        if self.local_tasks is None:
            return 0
        changed = self.local_tasks.reorder(task_ids)
        self._wake.set()
        return changed

    def local_asset(self, task_id: str, kind: str) -> Path | None:
        return self.local_tasks.asset_path(task_id, kind) if self.local_tasks else None

    @staticmethod
    def _is_quota_error(error: Exception) -> bool:
        return (
            isinstance(error, FirebaseRestError)
            and (
                getattr(error, "code", "") == "FIREBASE_QUOTA_EXHAUSTED"
                or int(getattr(error, "details", {}).get("http_status") or 0) == 429
            )
        )

    def _activate_cloud_backoff(self) -> None:
        schedule = (5 * 60, 30 * 60, 2 * 60 * 60, 6 * 60 * 60)
        self._cloud_failure_count += 1
        self._cloud_backoff_seconds = schedule[min(self._cloud_failure_count - 1, len(schedule) - 1)]
        self._cloud_backoff_until = time.monotonic() + self._cloud_backoff_seconds

    def _clear_cloud_backoff(self) -> None:
        self._cloud_failure_count = 0
        self._cloud_backoff_until = 0.0
        self._cloud_backoff_seconds = 0

    def _next_delay(self, worked: bool) -> float:
        if worked:
            return 1
        remaining = self._cloud_backoff_until - time.monotonic()
        if remaining > 0:
            return remaining
        settings = self.policy.load()
        if self.last_state in {
            "IDLE", "WAITING_FOR_PAIRING", "WAITING_FOR_VOICE", "WAITING_FOR_MAC",
            "MEMORY_PRESSURE", "WAITING_FOR_AC_POWER", "FREE_QUOTA_LOCAL_READY",
        }:
            return settings.idle_poll_seconds
        return settings.poll_seconds

    def _process_deletion(self, deletion: CloudDeletion) -> bool:
        claimed = self.tasks.claim_deletion(deletion)
        try:
            if claimed.storage_mode == "PRIVATE_FIRESTORE":
                private = FirestorePrivateAssetPublisher(
                    self.tasks.client,
                    claimed.owner_uid,
                    task_id=claimed.task_id,
                )
                if claimed.private_audio_key:
                    private.delete_private(
                        claimed.private_audio_key,
                        part_count=claimed.private_audio_parts or None,
                    )
                if claimed.private_timeline_key:
                    private.delete_private(
                        claimed.private_timeline_key,
                        part_count=claimed.private_timeline_parts or None,
                    )
            else:
                publisher = GitHubAudiobookPublisher(self.repository)
                if claimed.asset_id is not None:
                    publisher.audio.delete_verified(claimed.asset_id)
                if claimed.timeline_asset_id is not None and claimed.timeline_url:
                    publisher.data.delete_persisted(
                        claimed.timeline_asset_id,
                        claimed.timeline_url,
                    )
            self.tasks.complete_deletion(claimed)
            self._ready_audio_seconds = None
            self.last_state = "AUDIO_DELETED"
            self.last_error = ""
        except GenerationError as error:
            retry_at = datetime.now(UTC) + self._retry_delay(error.code, claimed.attempt_count)
            self.tasks.fail_deletion(
                claimed,
                error_code=error.code,
                message=str(error),
                retry_at=retry_at,
            )
            self.last_state = "DELETE_RETRY"
            self.last_error = str(error)
        return True

    def _process_book_deletion(self, deletion: CloudBookDeletion) -> bool:
        claimed = self.tasks.claim_book_deletion(deletion)
        try:
            pending_audio = [
                item
                for item in self.tasks.audio_inventory(
                    claimed.owner_uid,
                    [claimed.book_id],
                )
                if item.status != "DELETED"
            ]
            if pending_audio:
                raise GenerationError(
                    "BOOK_AUDIO_DELETION_PENDING",
                    "书籍音频仍在安全删除中，稍后会自动继续。",
                )
            if claimed.private_text_key:
                FirestorePrivateAssetPublisher(
                    self.tasks.client,
                    claimed.owner_uid,
                    task_id=f"book-deletion:{claimed.book_id}",
                ).delete_private(
                    claimed.private_text_key,
                    part_count=claimed.private_text_parts or None,
                )
            if claimed.text_asset_id is not None and claimed.text_asset_url:
                GitHubRepositoryAssetPublisher(self.repository).delete_persisted(
                    claimed.text_asset_id,
                    claimed.text_asset_url,
                )
            self.library.remove(claimed.book_id)
            self.tasks.purge_book_records(claimed)
            self.tasks.complete_book_deletion(claimed)
            self._published_books.discard(claimed.book_id)
            self._ready_audio_seconds = None
            self.last_state = "BOOK_DELETED"
            self.last_error = ""
        except Exception as error:
            failure = error if isinstance(error, GenerationError) else GenerationError(
                "BOOK_DELETE_FAILED",
                "书籍文件暂时无法清理，稍后会自动重试。",
            )
            retry_at = datetime.now(UTC) + self._retry_delay(
                failure.code,
                claimed.attempt_count,
            )
            self.tasks.fail_book_deletion(
                claimed,
                error_code=failure.code,
                message=str(failure),
                retry_at=retry_at,
            )
            self.last_state = "BOOK_DELETE_RETRY"
            self.last_error = str(failure)
        return True

    def _periodic_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 6 * 60 * 60:
            return
        clean_expired_generation_files(self.work_root)
        self._last_cleanup = now

    def _periodic_retention(self, owner_uid: str) -> None:
        now = time.monotonic()
        if now - self._last_retention < 60 * 60:
            return
        queued = self.tasks.queue_expired_audio(owner_uid, self.library.book_ids())
        if queued:
            self._ready_audio_seconds = None
        self._last_retention = now

    def _audio_cache_is_full(self, owner_uid: str) -> bool:
        now = time.monotonic()
        if (
            self._ready_audio_seconds is None
            or now - self._last_capacity_check >= self.CAPACITY_CHECK_SECONDS
        ):
            self._ready_audio_seconds = self.tasks.ready_audio_seconds(
                owner_uid,
                self.library.book_ids(),
            )
            self._last_capacity_check = now
        return self._ready_audio_seconds >= self.MAX_READY_AUDIO_SECONDS

    def _maybe_reconcile(self, owner_uid: str) -> bool:
        now = time.monotonic()
        if now - self._last_reconciliation < 6 * 60 * 60:
            self.last_state = "IDLE"
            return False
        self._last_reconciliation = now
        try:
            report = AudioReconciler(self.tasks, self.repository).run(
                owner_uid,
                self.library.book_ids(),
            )
            self.last_state = "RECONCILED"
            self.last_error = (
                f"发现 {len(report.missing)} 个缺失、{len(report.damaged)} 个损坏、"
                f"{len(report.orphan)} 个孤儿资产；未自动删除。"
            ) if report.missing or report.damaged or report.orphan else ""
        except GenerationError as error:
            self.last_state = "RECONCILE_RETRY"
            self.last_error = str(error)
        return True

    @staticmethod
    def _retry_delay(code: str, attempt: int) -> timedelta:
        base = 5 * 60 if code == "GITHUB_LIMITED" else 30
        maximum = 6 * 60 * 60 if code == "GITHUB_LIMITED" else 30 * 60
        seconds = min(maximum, base * (2 ** max(0, min(attempt - 1, 8))))
        return timedelta(seconds=seconds)

    def status(self) -> dict[str, str | bool | int]:
        remaining = max(0, round(self._cloud_backoff_until - time.monotonic()))
        return {
            "state": self.last_state,
            "error": self.last_error,
            "model_loaded": bool(getattr(self._generator, "loaded", False)),
            "cloud_backoff_seconds": remaining,
            "local_pending_sync": (
                int(self.local_tasks.status()["pending_sync"])
                if self.local_tasks else 0
            ),
        }

    def model_loaded(self) -> bool:
        return bool(getattr(self._generator, "loaded", False))

    def _generator_for(self, profile: VoiceProfile) -> SegmentGenerator:
        if self._generator is None or self._voice_version != profile.voice_version:
            if self._generator:
                self._generator.unload()
            self._generator = self.generator_factory(profile)
            self._voice_version = profile.voice_version
        return self._generator

    def _unload_generator(self) -> None:
        generator = self._generator
        self._generator = None
        self._voice_version = ""
        if generator:
            generator.unload()

    def _sync_private_book_texts(self, owner_uid: str) -> bool:
        for book_id in self.library.book_ids():
            if book_id in self._published_books:
                continue
            book = self.library.get(book_id)
            if book is None or book.publication_mode is not PublicationMode.LOCAL_ONLY:
                self._published_books.add(book_id)
                continue
            self._ensure_book_text(owner_uid, "library-sync", book)
            self.last_state = "BOOK_TEXT_SYNCED"
            return True
        return False

    def _ensure_book_text(self, owner_uid: str, task_id: str, book: ParsedBook) -> None:
        if book.book_id in self._published_books:
            return
        existing = self.tasks.book_text_asset(owner_uid, book.book_id)
        if book.publication_mode is PublicationMode.LOCAL_ONLY:
            private_publisher = FirestorePrivateAssetPublisher(
                self.tasks.client,
                owner_uid,
                task_id=task_id,
            )
            asset = BookTextPublisher(
                self.work_root / "books",
                private_publisher,
                allow_local_only=True,
            ).publish(book)
            if existing is None or existing.sha256 != asset.sha256:
                self.tasks.record_book_text(owner_uid, book, asset)
                if existing and existing.private_key != asset.private_key:
                    private_publisher.delete_private(
                        existing.private_key,
                        part_count=existing.part_count or None,
                    )
        elif existing is None:
            public_publisher = GitHubRepositoryAssetPublisher(self.repository)
            asset = BookTextPublisher(
                self.work_root / "books",
                public_publisher,
            ).publish(book)
            self.tasks.record_book_text(owner_uid, book, asset)
        self._published_books.add(book.book_id)

    @staticmethod
    def _make_job(book: ParsedBook, task: CloudTask, profile: VoiceProfile) -> ChunkJob:
        batch = plan_generation_batch(
            book,
            start_segment_id=task.start_segment_id,
            target_seconds=min(task.target_seconds, 600),
            chunk_seconds=600,
        )
        if not batch.chunks:
            raise GenerationError("BOOK_FINISHED", "这本书已经全部生成完成。")
        planned = batch.chunks[0]
        by_id = {
            segment.segment_id: segment
            for chapter in book.chapters
            for segment in chapter.segments
        }
        segments = tuple(
            SegmentJob(
                segment_id=by_id[segment_id].segment_id,
                chapter_id=by_id[segment_id].chapter_id,
                order=by_id[segment_id].order,
                spoken_text=by_id[segment_id].spoken_text,
                text_hash=by_id[segment_id].text_hash,
            )
            for segment_id in planned.segment_ids
        )
        return ChunkJob(
            task_id=task.task_id,
            book_id=task.book_id,
            chunk_id=planned.chunk_id,
            chapter_id=segments[0].chapter_id,
            publication_mode=book.publication_mode,
            voice_version=profile.voice_version,
            attempt_id=task.attempt_id,
            lease_token=task.lease_token,
            deletion_generation=task.deletion_generation,
            segments=segments,
        )
