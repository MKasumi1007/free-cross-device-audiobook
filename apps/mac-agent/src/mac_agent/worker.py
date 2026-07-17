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
from .generation import ChunkJob, ChunkPipeline, GenerationError, SegmentGenerator, SegmentJob
from .library import LocalLibrary
from .private_assets import FirestorePrivateAssetPublisher
from .repository_assets import GitHubAudiobookPublisher, GitHubRepositoryAssetPublisher
from .reconciliation import AudioReconciler
from .resources import ResourcePolicy
from .task_cloud import CloudDeletion, CloudTask, FirestoreWorkerTasks
from .voice import VoiceProfile, VoiceRegistry


GeneratorFactory = Callable[[VoiceProfile], SegmentGenerator]


class RenewingFence:
    def __init__(self, tasks: FirestoreWorkerTasks, task: CloudTask) -> None:
        self.tasks = tasks
        self.task = task
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None

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
            if current is None:
                raise GenerationError("STALE_LEASE", "生成任务已经不存在。")
            self.task = self.tasks.transition(current, status)

    def _run_heartbeat(self) -> None:
        while not self._stop.wait(5 * 60):
            with self._lock:
                current = self.tasks.get(self.task.owner_uid, self.task.task_id)
                if current is None or current.lease_token != self.task.lease_token:
                    return
                self.task = self.tasks.renew(current)


class MacGenerationWorker:
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
    ) -> None:
        self.tasks = tasks
        self.library = library
        self.voices = voices
        self.policy = policy
        self.generator_factory = generator_factory
        self.work_root = work_root
        self.repository = repository
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._generator: SegmentGenerator | None = None
        self._voice_version = ""
        self._published_books: set[str] = set()
        self.last_state = "IDLE"
        self.last_error = ""
        self._last_presence = 0.0
        self._last_cleanup = float("-inf")
        self._last_reconciliation = float("-inf")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="audiobook-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._generator:
            self._generator.unload()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
                self.last_error = ""
            except Exception:
                worked = False
                self.last_state = "ERROR"
                self.last_error = "后台生成暂时中断，稍后会自动重试。"
            delay = 1 if worked else self.policy.load().poll_seconds
            self._stop.wait(delay)

    def run_once(self) -> bool:
        owner_uid = self.tasks.active_owner()
        if owner_uid is None:
            self.last_state = "WAITING_FOR_PAIRING"
            return False
        if time.monotonic() - self._last_presence >= 4 * 60:
            self.tasks.touch_presence()
            self._last_presence = time.monotonic()
        self._periodic_cleanup()
        deletion = self.tasks.next_deletion(owner_uid)
        if deletion is not None:
            return self._process_deletion(deletion)
        task = self.tasks.next_task(owner_uid)
        if task is None:
            return self._maybe_reconcile(owner_uid)
        profile = self.voices.load()
        if profile is None or not profile.confirmed:
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
        task = self.tasks.transition(task, "GENERATING")
        fence = RenewingFence(self.tasks, task)
        fence.start()
        try:
            self._ensure_book_text(fence.task, book)
            job = self._make_job(book, task, profile)
            generator = self._generator_for(profile)
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
            fence.state("READY")
            self.last_state = "READY"
            return True
        except GenerationError as error:
            current = self.tasks.get(task.owner_uid, task.task_id)
            if current and current.lease_token == task.lease_token:
                retry_at = datetime.now(UTC) + self._retry_delay(error.code, current.attempt_id)
                self.tasks.transition(
                    current,
                    "FAILED_RETRYABLE",
                    error_code=error.code,
                    error_message="生成暂时中断，稍后会从检查点继续。",
                    retry_not_before=retry_at,
                )
            self.last_state = "FAILED_RETRYABLE"
            self.last_error = str(error)
            return True
        finally:
            fence.stop()

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

    def _periodic_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 6 * 60 * 60:
            return
        clean_expired_generation_files(self.work_root)
        self._last_cleanup = now

    def _maybe_reconcile(self, owner_uid: str) -> bool:
        now = time.monotonic()
        if now - self._last_reconciliation < 6 * 60 * 60:
            self.last_state = "IDLE"
            return False
        self._last_reconciliation = now
        try:
            report = AudioReconciler(self.tasks, self.repository).run(owner_uid)
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

    def status(self) -> dict[str, str | bool]:
        return {
            "state": self.last_state,
            "error": self.last_error,
            "model_loaded": bool(getattr(self._generator, "loaded", False)),
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

    def _ensure_book_text(self, task: CloudTask, book: ParsedBook) -> None:
        if book.book_id in self._published_books:
            return
        existing = self.tasks.book_text_asset(task.owner_uid, book.book_id)
        if existing is None:
            if book.publication_mode is PublicationMode.LOCAL_ONLY:
                private_publisher = FirestorePrivateAssetPublisher(
                    self.tasks.client,
                    task.owner_uid,
                    task_id=task.task_id,
                )
                asset = BookTextPublisher(
                    self.work_root / "books",
                    private_publisher,
                    allow_local_only=True,
                ).publish(book)
            else:
                public_publisher = GitHubRepositoryAssetPublisher(self.repository)
                asset = BookTextPublisher(
                    self.work_root / "books",
                    public_publisher,
                ).publish(book)
            self.tasks.record_book_text(task.owner_uid, book, asset)
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
