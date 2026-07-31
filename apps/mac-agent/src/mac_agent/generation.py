from __future__ import annotations

import gzip
import json
import os
import subprocess
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Callable, Protocol

from audiobook_core.models import PublicationMode

from .error_reporting import AgentOperationError, completed_process_details, reporter
from .media import duration_from_ffmpeg_progress, ffmpeg_duration_command
from .tts_text import split_generation_text


class GenerationError(AgentOperationError):
    pass


@dataclass(frozen=True)
class SegmentJob:
    segment_id: str
    chapter_id: str
    order: int
    spoken_text: str
    text_hash: str


@dataclass(frozen=True)
class ChunkJob:
    task_id: str
    book_id: str
    chunk_id: str
    chapter_id: str
    publication_mode: PublicationMode
    voice_version: str
    attempt_id: int
    lease_token: str
    deletion_generation: int
    segments: tuple[SegmentJob, ...]


@dataclass(frozen=True)
class GeneratedAudio:
    duration_seconds: float
    sample_rate: int


@dataclass(frozen=True)
class PublishedAsset:
    asset_id: int
    name: str
    url: str
    byte_size: int
    sha256: str
    created: bool = True
    storage_mode: str = "PUBLIC_GITHUB"
    private_key: str = ""
    part_count: int = 0
    content_type: str = ""


@dataclass(frozen=True)
class PublishedChunk:
    chunk_id: str
    duration_seconds: float
    audio: PublishedAsset
    timeline: PublishedAsset
    reused: bool = False


class SegmentGenerator(Protocol):
    def generate(
        self,
        text: str,
        output_wav: Path,
        on_progress: Callable[[int, int, float], None] | None = None,
    ) -> GeneratedAudio: ...

    def unload(self) -> None: ...


class Publisher(Protocol):
    storage_mode: str

    def publish(self, book_id: str, path: Path, asset_name: str) -> PublishedAsset: ...

    def delete(self, book_id: str, asset_id: int) -> None: ...


class LeaseFence(Protocol):
    def assert_current(self, job: ChunkJob) -> None: ...


class MediaEncoder(Protocol):
    def encode(self, wav_paths: list[Path], destination: Path) -> float: ...


class PipelineObserver(Protocol):
    def state(self, status: str) -> None: ...

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
    ) -> None: ...


class NullPipelineObserver:
    def state(self, status: str) -> None:
        del status

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
        del (
            completed_units,
            total_units,
            completed_segments,
            total_segments,
            current_segment_id,
            current_segment_order,
            current_piece,
            current_piece_total,
            generated_audio_seconds,
        )


class FfmpegMediaEncoder:
    def encode(self, wav_paths: list[Path], destination: Path) -> float:
        if not wav_paths:
            raise GenerationError("EMPTY_CHUNK", "音频块没有可生成的正文。")
        concat_file = destination.with_suffix(".concat.txt")
        concat_file.write_text(
            "".join(f"file '{self._escape(path.resolve())}'\n" for path in wav_paths),
            encoding="utf-8",
        )
        try:
            try:
                result = subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat_file),
                        "-c:a",
                        "aac",
                        "-b:a",
                        "64k",
                        "-movflags",
                        "+faststart",
                        str(destination),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except FileNotFoundError as error:
                raise GenerationError(
                    "FFMPEG_MISSING",
                    "没有找到 FFmpeg，请在系统状态中运行自动修复。",
                    details={"destination": str(destination)},
                ) from error
        finally:
            concat_file.unlink(missing_ok=True)
        if result.returncode != 0 or not destination.is_file():
            raise GenerationError(
                "FFMPEG_ENCODING_FAILED",
                "M4A 编码失败，已保留逐段检查点。",
                details=completed_process_details(result),
            )
        try:
            probe = subprocess.run(
                ffmpeg_duration_command(destination),
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as error:
            raise GenerationError(
                "FFMPEG_MISSING",
                "没有找到 FFmpeg，无法校验生成音频。",
                details={"destination": str(destination)},
            ) from error
        try:
            duration = duration_from_ffmpeg_progress(probe)
        except (TypeError, ValueError) as error:
            raise GenerationError(
                "AUDIO_VALIDATION_FAILED",
                "无法验证生成音频的时长。",
                details=completed_process_details(probe),
            ) from error
        return duration

    @staticmethod
    def _escape(path: Path) -> str:
        return str(path).replace("'", "'\\''")


class SingleTaskLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> SingleTaskLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_lock()
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(self.fd, str(os.getpid()).encode("ascii"))
        except FileExistsError as error:
            raise GenerationError("TTS_BUSY", "这台 Mac 已经在生成另一个音频块。") from error
        return self

    def __exit__(self, *_args: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)

    def _remove_stale_lock(self) -> None:
        if not self.path.exists():
            return
        try:
            pid = int(self.path.read_text(encoding="ascii").strip())
            os.kill(pid, 0)
        except (OSError, ValueError):
            self.path.unlink(missing_ok=True)


class ChunkPipeline:
    def __init__(
        self,
        root: Path,
        generator: SegmentGenerator,
        publisher: Publisher,
        fence: LeaseFence,
        *,
        encoder: MediaEncoder | None = None,
        observer: PipelineObserver | None = None,
    ) -> None:
        self.root = root
        self.generator = generator
        self.publisher = publisher
        self.fence = fence
        self.encoder = encoder or FfmpegMediaEncoder()
        self.observer = observer or NullPipelineObserver()

    def run(self, job: ChunkJob) -> PublishedChunk:
        self._validate(job)
        work = self.root / "tasks" / job.task_id / job.chunk_id
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        with SingleTaskLock(self.root / "active-task.lock"):
            receipt = self._load_receipt(work, job)
            if receipt is not None:
                return replace(receipt, reused=True)
            self.fence.assert_current(job)
            timeline = self._generate_segments(work, job)
            self.fence.assert_current(job)

            audio_path = work / "chunk.m4a"
            self.observer.state("ENCODING")
            duration = self.encoder.encode(
                [work / "segments" / f"{item.segment_id}.wav" for item in job.segments],
                audio_path,
            )
            timeline_path = work / "timeline.json.gz"
            self._write_timeline(timeline_path, job, timeline, duration)
            audio_hash = self._hash_file(audio_path)
            timeline_hash = self._hash_file(timeline_path)
            audio_name = f"audio-{job.chunk_id}-{audio_hash[:12]}.m4a"
            timeline_name = f"timeline-{job.chunk_id}-{timeline_hash[:12]}.json.gz"

            self.fence.assert_current(job)
            self.observer.state("UPLOADING")
            uploaded: list[PublishedAsset] = []
            try:
                audio_asset = self.publisher.publish(job.book_id, audio_path, audio_name)
                if audio_asset.created:
                    uploaded.append(audio_asset)
                self._validate_published_asset(audio_asset)
                timeline_asset = self.publisher.publish(job.book_id, timeline_path, timeline_name)
                if timeline_asset.created:
                    uploaded.append(timeline_asset)
                self._validate_published_asset(timeline_asset)
                self.fence.assert_current(job)
            except Exception as publish_error:
                for asset in uploaded:
                    try:
                        self.publisher.delete(job.book_id, asset.asset_id)
                    except Exception as rollback_error:
                        reporter.record(
                            "generation.rollback_publish",
                            rollback_error,
                            code=getattr(rollback_error, "code", "PUBLISH_ROLLBACK_FAILED"),
                            details={
                                "book_id": job.book_id,
                                "task_id": job.task_id,
                                "asset_id": asset.asset_id,
                                "original_error": repr(publish_error),
                            },
                        )
                raise

            published = PublishedChunk(
                chunk_id=job.chunk_id,
                duration_seconds=duration,
                audio=audio_asset,
                timeline=timeline_asset,
            )
            self._write_receipt(work, job, published)
            self._clean_temporary_audio(work, job)
            return published

    def _generate_segments(self, work: Path, job: ChunkJob) -> list[dict[str, str | int | float]]:
        segment_dir = work / "segments"
        segment_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        checkpoint = self._load_checkpoint(work, job)
        completed = {str(item["segment_id"]): item for item in checkpoint}
        elapsed = sum(float(item["duration_seconds"]) for item in checkpoint)
        timeline = list(checkpoint)
        piece_counts = {
            segment.segment_id: max(1, len(split_generation_text(segment.spoken_text)))
            for segment in job.segments
        }
        total_units = sum(piece_counts.values())
        completed_units = sum(
            piece_counts.get(str(item["segment_id"]), 1)
            for item in checkpoint
        )
        completed_segments = len(checkpoint)
        for segment in job.segments:
            wav = segment_dir / f"{segment.segment_id}.wav"
            existing = completed.get(segment.segment_id)
            if existing and wav.is_file() and self._hash_file(wav) == existing.get("wav_sha256"):
                continue
            self.fence.assert_current(job)
            temporary = wav.with_suffix(".tmp.wav")
            segment_piece_total = piece_counts[segment.segment_id]

            def report_piece(
                piece: int,
                piece_total: int,
                segment_audio_seconds: float,
            ) -> None:
                self.observer.progress(
                    completed_units=completed_units + piece,
                    total_units=total_units,
                    completed_segments=completed_segments,
                    total_segments=len(job.segments),
                    current_segment_id=segment.segment_id,
                    current_segment_order=segment.order,
                    current_piece=piece,
                    current_piece_total=piece_total,
                    generated_audio_seconds=elapsed + segment_audio_seconds,
                )

            generated = self.generator.generate(
                segment.spoken_text,
                temporary,
                report_piece,
            )
            if generated.duration_seconds <= 0 or not temporary.is_file():
                temporary.unlink(missing_ok=True)
                raise GenerationError("TTS_EMPTY_OUTPUT", "语音模型没有生成有效音频。")
            os.replace(temporary, wav)
            wav.chmod(0o600)
            item: dict[str, str | int | float] = {
                "segment_id": segment.segment_id,
                "chapter_id": segment.chapter_id,
                "segment_order": segment.order,
                "start_seconds": elapsed,
                "end_seconds": elapsed + generated.duration_seconds,
                "duration_seconds": generated.duration_seconds,
                "text_hash": segment.text_hash,
                "wav_sha256": self._hash_file(wav),
            }
            timeline = [old for old in timeline if old["segment_id"] != segment.segment_id]
            timeline.append(item)
            timeline.sort(key=lambda value: int(value["segment_order"]))
            elapsed = sum(float(value["duration_seconds"]) for value in timeline)
            self._write_json_atomic(work / "checkpoint.json", self._checkpoint_payload(job, timeline))
            completed_units += segment_piece_total
            completed_segments += 1
            self.observer.progress(
                completed_units=completed_units,
                total_units=total_units,
                completed_segments=completed_segments,
                total_segments=len(job.segments),
                current_segment_id=segment.segment_id,
                current_segment_order=segment.order,
                current_piece=segment_piece_total,
                current_piece_total=segment_piece_total,
                generated_audio_seconds=elapsed,
            )
            discard_checkpoint = getattr(self.generator, "discard_checkpoint", None)
            if callable(discard_checkpoint):
                discard_checkpoint(temporary)
        return timeline

    def _load_checkpoint(self, work: Path, job: ChunkJob) -> list[dict[str, str | int | float]]:
        path = work / "checkpoint.json"
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if value.get("job_fingerprint") != self._fingerprint(job):
            return []
        raw = value.get("segments", [])
        return raw if isinstance(raw, list) else []

    def _load_receipt(self, work: Path, job: ChunkJob) -> PublishedChunk | None:
        path = work / "published.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if value.get("job_fingerprint") != self._fingerprint(job):
            return None
        published = value.get("published")
        if not isinstance(published, dict):
            return None
        try:
            return PublishedChunk(
                chunk_id=str(published["chunk_id"]),
                duration_seconds=float(published["duration_seconds"]),
                audio=PublishedAsset(**published["audio"]),
                timeline=PublishedAsset(**published["timeline"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _write_receipt(self, work: Path, job: ChunkJob, published: PublishedChunk) -> None:
        self._write_json_atomic(
            work / "published.json",
            {"job_fingerprint": self._fingerprint(job), "published": asdict(published)},
        )

    def _write_timeline(
        self,
        path: Path,
        job: ChunkJob,
        timeline: list[dict[str, str | int | float]],
        duration: float,
    ) -> None:
        payload = {
            "schema_version": 1,
            "book_id": job.book_id,
            "chunk_id": job.chunk_id,
            "chapter_id": job.chapter_id,
            "voice_version": job.voice_version,
            "duration_seconds": duration,
            "segments": timeline,
        }
        temporary = path.with_suffix(".tmp.gz")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
        path.chmod(0o600)

    def _clean_temporary_audio(self, work: Path, job: ChunkJob) -> None:
        (work / "chunk.m4a").unlink(missing_ok=True)
        (work / "timeline.json.gz").unlink(missing_ok=True)
        for segment in job.segments:
            (work / "segments" / f"{segment.segment_id}.wav").unlink(missing_ok=True)
        segment_dir = work / "segments"
        if segment_dir.exists() and not any(segment_dir.iterdir()):
            segment_dir.rmdir()

    def _validate(self, job: ChunkJob) -> None:
        storage_mode = getattr(self.publisher, "storage_mode", "PUBLIC_GITHUB")
        if storage_mode == "LOCAL_MAC":
            if not job.segments or any(not segment.spoken_text for segment in job.segments):
                raise GenerationError("EMPTY_CHUNK", "音频块没有可朗读正文。")
            if not job.lease_token or job.attempt_id <= 0:
                raise GenerationError("LEASE_REQUIRED", "生成任务没有有效租约。")
            return
        if (
            job.publication_mode is PublicationMode.LOCAL_ONLY
            and storage_mode != "PRIVATE_FIRESTORE"
        ):
            raise GenerationError(
                "PRIVATE_STORAGE_REQUIRED",
                "未确认传播权的书只能生成到账号私有区，不能公开发布。",
            )
        if (
            job.publication_mode is PublicationMode.PUBLIC_RIGHTS_CONFIRMED
            and storage_mode != "PUBLIC_GITHUB"
        ):
            raise GenerationError("STORAGE_MODE_MISMATCH", "音频发布方式与书籍权利设置不一致。")
        if not job.segments or any(not segment.spoken_text for segment in job.segments):
            raise GenerationError("EMPTY_CHUNK", "音频块没有可朗读正文。")
        if not job.lease_token or job.attempt_id <= 0:
            raise GenerationError("LEASE_REQUIRED", "生成任务没有有效租约。")

    def _validate_published_asset(self, asset: PublishedAsset) -> None:
        expected = getattr(self.publisher, "storage_mode", "PUBLIC_GITHUB")
        if asset.storage_mode != expected:
            raise GenerationError("STORAGE_MODE_MISMATCH", "发布结果进入了错误的存储区域。")

    def _checkpoint_payload(
        self,
        job: ChunkJob,
        timeline: list[dict[str, str | int | float]],
    ) -> dict[str, object]:
        return {
            "job_fingerprint": self._fingerprint(job),
            "voice_version": job.voice_version,
            "segments": timeline,
        }

    @staticmethod
    def _write_json_atomic(path: Path, value: object) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _fingerprint(job: ChunkJob) -> str:
        payload = {
            "book_id": job.book_id,
            "chunk_id": job.chunk_id,
            "voice_version": job.voice_version,
            "deletion_generation": job.deletion_generation,
            "segments": [(item.segment_id, item.text_hash) for item in job.segments],
        }
        return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
