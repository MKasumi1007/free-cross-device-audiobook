from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from audiobook_core.models import ParsedBook
from audiobook_core.planning import plan_generation_batch

from .generation import ChunkJob, ChunkPipeline, GenerationError, SegmentGenerator, SegmentJob
from .library import LocalLibrary
from .release_assets import GitHubReleasePublisher
from .resources import ResourcePolicy
from .task_cloud import CloudTask, FirestoreWorkerTasks
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
        self.last_state = "IDLE"
        self.last_error = ""

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
        profile = self.voices.load()
        if profile is None or not profile.confirmed:
            self.last_state = "WAITING_FOR_VOICE"
            return False
        task = self.tasks.next_task(owner_uid)
        if task is None:
            self.last_state = "IDLE"
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
        task = self.tasks.transition(task, "GENERATING")
        fence = RenewingFence(self.tasks, task)
        fence.start()
        try:
            job = self._make_job(book, task, profile)
            generator = self._generator_for(profile)
            pipeline = ChunkPipeline(
                self.work_root,
                generator,
                GitHubReleasePublisher(self.repository),
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
                self.tasks.transition(
                    current,
                    "FAILED_RETRYABLE",
                    error_code=error.code,
                    error_message="生成暂时中断，稍后会从检查点继续。",
                )
            self.last_state = "FAILED_RETRYABLE"
            self.last_error = str(error)
            return True
        finally:
            fence.stop()

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
