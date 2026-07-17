from __future__ import annotations

import secrets
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from .firebase_rest import FirebaseRestClient
from audiobook_core.models import ParsedBook

from .generation import ChunkJob, GenerationError, PublishedAsset, PublishedChunk


@dataclass(frozen=True)
class CloudTask:
    owner_uid: str
    task_id: str
    book_id: str
    status: str
    priority: int
    attempt_id: int
    deletion_generation: int
    start_segment_id: str | None
    target_seconds: float
    voice_version: str
    pause_reason: str = ""
    lease_owner: str = ""
    lease_token: str = ""
    lease_deadline: datetime | None = None
    retry_not_before: datetime | None = None
    update_time: str = ""


@dataclass(frozen=True)
class CloudDeletion:
    owner_uid: str
    request_id: str
    book_id: str
    chunk_id: str
    task_id: str
    status: str
    attempt_count: int
    deletion_generation: int
    asset_id: int | None
    asset_url: str
    timeline_asset_id: int | None
    timeline_url: str
    lease_owner: str = ""
    lease_token: str = ""
    lease_deadline: datetime | None = None
    retry_not_before: datetime | None = None
    update_time: str = ""


@dataclass(frozen=True)
class RemoteAudioRecord:
    owner_uid: str
    book_id: str
    chunk_id: str
    status: str
    asset_id: int | None
    asset_url: str
    byte_size: int
    timeline_asset_id: int | None
    timeline_url: str


class FirestoreWorkerTasks:
    LEASE_SECONDS = 20 * 60
    AUTO_RESUME_PAUSES = frozenset({"WAITING_FOR_AC_POWER", "MEMORY_PRESSURE", "WAITING_FOR_MAC"})

    def __init__(self, client: FirebaseRestClient) -> None:
        self.client = client

    def active_owner(self) -> str | None:
        identity = self.client.authenticate()
        _, value = self.client._json_request(
            "读取 Mac 绑定",
            "GET",
            self._document_url(f"workerLinks/{identity.local_id}"),
            id_token=identity.id_token,
            allowed_statuses=frozenset({200, 404}),
        )
        fields = self._fields(value)
        if not fields or fields.get("revoked_at") is not None:
            return None
        owner = fields.get("owner_uid")
        return str(owner) if owner else None

    def touch_presence(self) -> None:
        identity = self.client.authenticate()
        payload = {
            "writes": [
                {
                    "transform": {
                        "document": self._document_name(f"workerLinks/{identity.local_id}"),
                        "fieldTransforms": [
                            {"fieldPath": "last_seen_at", "setToServerValue": "REQUEST_TIME"}
                        ],
                    }
                }
            ]
        }
        self.client._json_request(
            "更新 Mac 在线状态",
            "POST",
            self._commit_url(),
            payload=payload,
            id_token=identity.id_token,
        )

    def next_task(self, owner_uid: str) -> CloudTask | None:
        tasks = self._query_by_status(owner_uid, ["QUEUED", "FAILED_RETRYABLE", "PAUSED"])
        tasks = [
            task
            for task in tasks
            if task.status != "PAUSED" or task.pause_reason in self.AUTO_RESUME_PAUSES
        ]
        if not tasks:
            tasks = self._query_by_status(
                owner_uid,
                ["LEASED", "GENERATING", "ENCODING", "UPLOADING"],
            )
        now = datetime.now(UTC)
        claimable = [
            task
            for task in tasks
            if (
                task.status in {"QUEUED", "FAILED_RETRYABLE", "PAUSED"}
                and (task.retry_not_before is None or task.retry_not_before <= now)
            )
            or (
                task.status in {"LEASED", "GENERATING", "ENCODING", "UPLOADING"}
                and task.lease_deadline is not None
                and task.lease_deadline <= now
            )
        ]
        claimable.sort(key=lambda task: (-task.priority, task.task_id))
        return claimable[0] if claimable else None

    def next_deletion(self, owner_uid: str) -> CloudDeletion | None:
        requests = self._query_deletions_by_status(owner_uid, ["QUEUED", "FAILED_RETRYABLE"])
        now = datetime.now(UTC)
        claimable = [
            item for item in requests
            if item.retry_not_before is None or item.retry_not_before <= now
        ]
        if not claimable:
            active = self._query_deletions_by_status(owner_uid, ["PROCESSING"])
            claimable = [
                item for item in active
                if item.lease_deadline is not None and item.lease_deadline <= now
            ]
        claimable.sort(key=lambda item: item.request_id)
        return claimable[0] if claimable else None

    def claim(self, task: CloudTask) -> CloudTask:
        identity = self.client.authenticate()
        lease_token = secrets.token_urlsafe(32)
        deadline = datetime.now(UTC) + timedelta(seconds=self.LEASE_SECONDS)
        fields: dict[str, dict[str, Any]] = {
            "status": self._string("LEASED"),
            "attempt_id": self._integer(task.attempt_id + 1),
            "lease_owner": self._string(identity.local_id),
            "lease_token": self._string(lease_token),
            "lease_deadline": self._timestamp(deadline),
            "pause_reason": {"nullValue": None},
        }
        update_time = self._commit_update(
            task,
            fields,
            ["status", "attempt_id", "lease_owner", "lease_token", "lease_deadline", "pause_reason"],
        )
        return CloudTask(
            **{
                **task.__dict__,
                "status": "LEASED",
                "attempt_id": task.attempt_id + 1,
                "lease_owner": identity.local_id,
                "lease_token": lease_token,
                "lease_deadline": deadline,
                "pause_reason": "",
                "update_time": update_time,
            }
        )

    def claim_deletion(self, deletion: CloudDeletion) -> CloudDeletion:
        identity = self.client.authenticate()
        lease_token = secrets.token_urlsafe(32)
        deadline = datetime.now(UTC) + timedelta(seconds=self.LEASE_SECONDS)
        changes = {
            "status": "PROCESSING",
            "attempt_count": deletion.attempt_count + 1,
            "lease_owner": identity.local_id,
            "lease_token": lease_token,
            "lease_deadline": deadline,
            "retry_not_before": None,
        }
        update_time = self._commit_deletion_update(deletion, changes)
        return replace(
            deletion,
            status="PROCESSING",
            attempt_count=deletion.attempt_count + 1,
            lease_owner=identity.local_id,
            lease_token=lease_token,
            lease_deadline=deadline,
            retry_not_before=None,
            update_time=update_time,
        )

    def fail_deletion(
        self,
        deletion: CloudDeletion,
        *,
        error_code: str,
        message: str,
        retry_at: datetime,
    ) -> CloudDeletion:
        changes = {
            "status": "FAILED_RETRYABLE",
            "error_code": error_code,
            "error_message": message,
            "retry_not_before": retry_at,
        }
        update_time = self._commit_deletion_update(deletion, changes)
        return replace(
            deletion,
            status="FAILED_RETRYABLE",
            retry_not_before=retry_at,
            update_time=update_time,
        )

    def complete_deletion(self, deletion: CloudDeletion) -> None:
        identity = self.client.authenticate()
        chunk_path = (
            f"users/{deletion.owner_uid}/books/{deletion.book_id}"
            f"/audioChunks/{deletion.chunk_id}"
        )
        _, chunk_document = self.client._json_request(
            "核对待删音频记录",
            "GET",
            self._document_url(chunk_path),
            id_token=identity.id_token,
            allowed_statuses=frozenset({200, 404}),
        )
        if not chunk_document.get("name"):
            raise GenerationError("AUDIO_METADATA_MISSING", "待删音频记录不存在，已停止处理。")
        current = self._fields(chunk_document)
        if current.get("status") not in {"DELETING", "DELETED"}:
            raise GenerationError("DELETION_BARRIER_CHANGED", "删除代次已经变化，旧请求不会继续执行。")
        if (
            current.get("deletion_request_id") != deletion.request_id
            or int(current.get("deletion_generation") or -1) != deletion.deletion_generation
        ):
            raise GenerationError("DELETION_BARRIER_CHANGED", "删除代次已经变化，旧请求不会继续执行。")

        request_path = (
            f"users/{deletion.owner_uid}/audioDeletionRequests/{deletion.request_id}"
        )
        request_write: dict[str, Any] = {
            "update": {
                "name": self._document_name(request_path),
                "fields": {"status": self._string("DONE")},
            },
            "updateMask": {"fieldPaths": ["status"]},
            "updateTransforms": [
                {"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"},
                {"fieldPath": "completed_at", "setToServerValue": "REQUEST_TIME"},
            ],
        }
        if deletion.update_time:
            request_write["currentDocument"] = {"updateTime": deletion.update_time}
        writes: list[dict[str, Any]] = [request_write]
        if current.get("status") != "DELETED":
            chunk_write: dict[str, Any] = {
                "update": {
                    "name": self._document_name(chunk_path),
                    "fields": {
                        "status": self._string("DELETED"),
                        "asset_id": {"nullValue": None},
                        "asset_url": {"nullValue": None},
                        "timeline_asset_id": {"nullValue": None},
                        "timeline_url": {"nullValue": None},
                    },
                },
                "updateMask": {
                    "fieldPaths": [
                        "status", "asset_id", "asset_url",
                        "timeline_asset_id", "timeline_url",
                    ]
                },
                "updateTransforms": [
                    {"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"},
                    {"fieldPath": "deleted_at", "setToServerValue": "REQUEST_TIME"},
                ],
                "currentDocument": {"updateTime": str(chunk_document["updateTime"])},
            }
            writes.append(chunk_write)
        self.client._json_request(
            "完成远程音频删除",
            "POST",
            self._commit_url(),
            payload={"writes": writes},
            id_token=identity.id_token,
        )

    def transition(self, task: CloudTask, status: str, **changes: Any) -> CloudTask:
        fields: dict[str, dict[str, Any]] = {"status": self._string(status)}
        masks = ["status"]
        for name, value in changes.items():
            masks.append(name)
            fields[name] = self._value(value)
        update_time = self._commit_update(task, fields, masks)
        known = {field.name for field in fields_of_cloud_task()}
        task_changes = {name: value for name, value in changes.items() if name in known}
        return replace(task, status=status, update_time=update_time, **task_changes)

    def renew(self, task: CloudTask) -> CloudTask:
        deadline = datetime.now(UTC) + timedelta(seconds=self.LEASE_SECONDS)
        return self.transition(task, task.status, lease_deadline=deadline)

    def assert_current(self, job: ChunkJob) -> None:
        owner = self.active_owner()
        if owner is None:
            raise GenerationError("WORKER_REVOKED", "这台 Mac 的连接已撤销。")
        current = self.get(owner, job.task_id)
        if (
            current is None
            or current.attempt_id != job.attempt_id
            or current.lease_token != job.lease_token
            or current.deletion_generation != job.deletion_generation
            or current.lease_deadline is None
            or current.lease_deadline <= datetime.now(UTC)
        ):
            raise GenerationError("STALE_LEASE", "任务租约已失效，迟到结果不会发布。")

    def get(self, owner_uid: str, task_id: str) -> CloudTask | None:
        identity = self.client.authenticate()
        path = f"users/{quote(owner_uid, safe='')}/generationRequests/{quote(task_id, safe='')}"
        _, value = self.client._json_request(
            "检查生成租约",
            "GET",
            self._document_url(path),
            id_token=identity.id_token,
            allowed_statuses=frozenset({200, 404}),
        )
        return self._task(owner_uid, value) if value.get("name") else None

    def record_ready(self, task: CloudTask, job: ChunkJob, published: PublishedChunk) -> None:
        identity = self.client.authenticate()
        path = f"users/{task.owner_uid}/books/{task.book_id}/audioChunks/{job.chunk_id}"
        _, existing = self.client._json_request(
            "检查可播放音频",
            "GET",
            self._document_url(path),
            id_token=identity.id_token,
            allowed_statuses=frozenset({200, 404}),
        )
        if existing.get("name"):
            current = self._fields(existing)
            if (
                current.get("status") == "READY"
                and current.get("asset_id") == published.audio.asset_id
                and current.get("sha256") == published.audio.sha256
                and current.get("timeline_asset_id") == published.timeline.asset_id
            ):
                return
            if not (
                current.get("status") == "FAILED_RETRYABLE"
                and current.get("task_id") == task.task_id
                and current.get("deletion_generation") == job.deletion_generation
            ):
                raise GenerationError("AUDIO_METADATA_CONFLICT", "已有音频记录与本次发布不一致。")
        fields = {
            "owner_uid": self._string(task.owner_uid),
            "task_id": self._string(task.task_id),
            "book_id": self._string(task.book_id),
            "chunk_id": self._string(job.chunk_id),
            "chapter_id": self._string(job.chapter_id),
            "status": self._string("READY"),
            "attempt_id": self._integer(task.attempt_id),
            "lease_token": self._string(task.lease_token),
            "start_segment_id": self._string(job.segments[0].segment_id),
            "end_segment_id": self._string(job.segments[-1].segment_id),
            "duration_seconds": self._double(published.duration_seconds),
            "asset_id": self._integer(published.audio.asset_id),
            "asset_url": self._string(published.audio.url),
            "sha256": self._string(published.audio.sha256),
            "byte_size": self._integer(published.audio.byte_size),
            "codec": self._string("AAC-LC/M4A"),
            "timeline_asset_id": self._integer(published.timeline.asset_id),
            "timeline_url": self._string(published.timeline.url),
            "timeline_sha256": self._string(published.timeline.sha256),
            "voice_version": self._string(job.voice_version),
            "deletion_generation": self._integer(job.deletion_generation),
        }
        write: dict[str, Any] = {
            "update": {"name": self._document_name(path), "fields": fields},
            "currentDocument": (
                {"updateTime": str(existing["updateTime"])}
                if existing.get("name")
                else {"exists": False}
            ),
            "updateTransforms": [
                {"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"},
                {"fieldPath": "completed_at", "setToServerValue": "REQUEST_TIME"},
            ],
        }
        payload = {"writes": [write]}
        self.client._json_request(
            "保存可播放音频",
            "POST",
            self._commit_url(),
            payload=payload,
            id_token=identity.id_token,
        )

    def book_text_asset(self, owner_uid: str, book_id: str) -> PublishedAsset | None:
        identity = self.client.authenticate()
        path = f"users/{owner_uid}/books/{book_id}"
        _, value = self.client._json_request(
            "检查远程正文",
            "GET",
            self._document_url(path),
            id_token=identity.id_token,
            allowed_statuses=frozenset({200, 404}),
        )
        fields = self._fields(value)
        if fields.get("text_status") != "READY" or not fields.get("text_asset_url"):
            return None
        try:
            return PublishedAsset(
                asset_id=int(fields["text_asset_id"]),
                name=str(fields.get("text_asset_name") or ""),
                url=str(fields["text_asset_url"]),
                byte_size=int(fields["text_byte_size"]),
                sha256=str(fields["text_sha256"]),
                created=False,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def record_book_text(
        self,
        owner_uid: str,
        book: ParsedBook,
        asset: PublishedAsset,
    ) -> None:
        identity = self.client.authenticate()
        path = f"users/{owner_uid}/books/{book.book_id}"
        fields = {
            "text_status": self._string("READY"),
            "text_asset_id": self._integer(asset.asset_id),
            "text_asset_name": self._string(asset.name),
            "text_asset_url": self._string(asset.url),
            "text_sha256": self._string(asset.sha256),
            "text_byte_size": self._integer(asset.byte_size),
            "text_schema_version": self._integer(1),
        }
        masks = list(fields)
        write = {
            "update": {"name": self._document_name(path), "fields": fields},
            "updateMask": {"fieldPaths": masks},
            "updateTransforms": [
                {"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"}
            ],
        }
        self.client._json_request(
            "保存远程正文信息",
            "POST",
            self._commit_url(),
            payload={"writes": [write]},
            id_token=identity.id_token,
        )

    def audio_inventory(self, owner_uid: str) -> list[RemoteAudioRecord]:
        identity = self.client.authenticate()
        owner_path = f"users/{quote(owner_uid, safe='')}"
        payload = {
            "structuredQuery": {
                "from": [{"collectionId": "audioChunks", "allDescendants": True}],
            }
        }
        _, value = self.client._json_request(
            "读取远程音频清单",
            "POST",
            f"{self._document_url(owner_path)}:runQuery",
            payload=payload,
            id_token=identity.id_token,
        )
        rows = value if isinstance(value, list) else []
        records: list[RemoteAudioRecord] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("document"), dict):
                continue
            fields = self._fields(row["document"])
            records.append(RemoteAudioRecord(
                owner_uid=owner_uid,
                book_id=str(fields.get("book_id") or ""),
                chunk_id=str(fields.get("chunk_id") or ""),
                status=str(fields.get("status") or ""),
                asset_id=int(fields["asset_id"]) if fields.get("asset_id") is not None else None,
                asset_url=str(fields.get("asset_url") or ""),
                byte_size=int(fields.get("byte_size") or 0),
                timeline_asset_id=(
                    int(fields["timeline_asset_id"])
                    if fields.get("timeline_asset_id") is not None
                    else None
                ),
                timeline_url=str(fields.get("timeline_url") or ""),
            ))
        return records

    def record_reconciliation(
        self,
        owner_uid: str,
        *,
        missing_assets: int,
        damaged_assets: int,
        orphan_assets: int,
        checked_books: int,
        summary: str,
    ) -> None:
        identity = self.client.authenticate()
        path = f"users/{owner_uid}/maintenance/audio-reconciliation"
        fields = {
            "owner_uid": self._string(owner_uid),
            "kind": self._string("AUDIO_RECONCILIATION"),
            "missing_assets": self._integer(missing_assets),
            "damaged_assets": self._integer(damaged_assets),
            "orphan_assets": self._integer(orphan_assets),
            "checked_books": self._integer(checked_books),
            "summary": self._string(summary[:12_000]),
        }
        self.client._json_request(
            "保存音频对账结果",
            "POST",
            self._commit_url(),
            payload={"writes": [{
                "update": {"name": self._document_name(path), "fields": fields},
                "updateMask": {"fieldPaths": list(fields)},
                "updateTransforms": [
                    {"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"}
                ],
            }]},
            id_token=identity.id_token,
        )

    def _commit_update(
        self,
        task: CloudTask,
        fields: dict[str, dict[str, Any]],
        masks: list[str],
    ) -> str:
        identity = self.client.authenticate()
        path = f"users/{task.owner_uid}/generationRequests/{task.task_id}"
        write: dict[str, Any] = {
            "update": {"name": self._document_name(path), "fields": fields},
            "updateMask": {"fieldPaths": masks},
            "updateTransforms": [{"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"}],
        }
        if task.update_time:
            write["currentDocument"] = {"updateTime": task.update_time}
        _, value = self.client._json_request(
            "更新生成任务",
            "POST",
            self._commit_url(),
            payload={"writes": [write]},
            id_token=identity.id_token,
        )
        results = value.get("writeResults", [])
        return str(results[0].get("updateTime") or "") if results else ""

    def _commit_deletion_update(
        self,
        deletion: CloudDeletion,
        changes: dict[str, Any],
    ) -> str:
        identity = self.client.authenticate()
        path = f"users/{deletion.owner_uid}/audioDeletionRequests/{deletion.request_id}"
        fields = {name: self._value(value) for name, value in changes.items()}
        write: dict[str, Any] = {
            "update": {"name": self._document_name(path), "fields": fields},
            "updateMask": {"fieldPaths": list(fields)},
            "updateTransforms": [
                {"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"}
            ],
        }
        if deletion.update_time:
            write["currentDocument"] = {"updateTime": deletion.update_time}
        _, value = self.client._json_request(
            "更新音频删除任务",
            "POST",
            self._commit_url(),
            payload={"writes": [write]},
            id_token=identity.id_token,
        )
        results = value.get("writeResults", [])
        return str(results[0].get("updateTime") or "") if results else ""

    def _task(self, owner_uid: str, document: dict[str, Any]) -> CloudTask:
        fields = self._fields(document)
        lease_deadline = fields.get("lease_deadline")
        retry_not_before = fields.get("retry_not_before")
        task_id = str(fields.get("task_id") or str(document.get("name", "")).rsplit("/", 1)[-1])
        return CloudTask(
            owner_uid=owner_uid,
            task_id=task_id,
            book_id=str(fields.get("book_id") or ""),
            status=str(fields.get("status") or ""),
            priority=int(fields.get("priority") or 0),
            attempt_id=int(fields.get("attempt_id") or 0),
            deletion_generation=int(fields.get("deletion_generation") or 0),
            start_segment_id=str(fields["start_segment_id"]) if fields.get("start_segment_id") else None,
            target_seconds=float(fields.get("target_seconds") or 18_000),
            voice_version=str(fields.get("voice_version") or ""),
            pause_reason=str(fields.get("pause_reason") or ""),
            lease_owner=str(fields.get("lease_owner") or ""),
            lease_token=str(fields.get("lease_token") or ""),
            lease_deadline=lease_deadline if isinstance(lease_deadline, datetime) else None,
            retry_not_before=(
                retry_not_before if isinstance(retry_not_before, datetime) else None
            ),
            update_time=str(document.get("updateTime") or ""),
        )

    def _deletion(self, owner_uid: str, document: dict[str, Any]) -> CloudDeletion:
        values = self._fields(document)
        lease_deadline = values.get("lease_deadline")
        retry_not_before = values.get("retry_not_before")
        request_id = str(
            values.get("request_id") or str(document.get("name", "")).rsplit("/", 1)[-1]
        )
        return CloudDeletion(
            owner_uid=owner_uid,
            request_id=request_id,
            book_id=str(values.get("book_id") or ""),
            chunk_id=str(values.get("chunk_id") or ""),
            task_id=str(values.get("task_id") or ""),
            status=str(values.get("status") or ""),
            attempt_count=int(values.get("attempt_count") or 0),
            deletion_generation=int(values.get("deletion_generation") or 0),
            asset_id=int(values["asset_id"]) if values.get("asset_id") is not None else None,
            asset_url=str(values.get("asset_url") or ""),
            timeline_asset_id=(
                int(values["timeline_asset_id"])
                if values.get("timeline_asset_id") is not None
                else None
            ),
            timeline_url=str(values.get("timeline_url") or ""),
            lease_owner=str(values.get("lease_owner") or ""),
            lease_token=str(values.get("lease_token") or ""),
            lease_deadline=lease_deadline if isinstance(lease_deadline, datetime) else None,
            retry_not_before=(
                retry_not_before if isinstance(retry_not_before, datetime) else None
            ),
            update_time=str(document.get("updateTime") or ""),
        )

    def _fields(self, document: dict[str, Any]) -> dict[str, Any]:
        return {name: self._decode(value) for name, value in document.get("fields", {}).items()}

    def _decode(self, value: dict[str, Any]) -> Any:
        if "nullValue" in value:
            return None
        if "stringValue" in value:
            return value["stringValue"]
        if "integerValue" in value:
            return int(value["integerValue"])
        if "doubleValue" in value:
            return float(value["doubleValue"])
        if "booleanValue" in value:
            return bool(value["booleanValue"])
        if "timestampValue" in value:
            return datetime.fromisoformat(str(value["timestampValue"]).replace("Z", "+00:00"))
        return None

    def _value(self, value: Any) -> dict[str, Any]:
        if value is None:
            return {"nullValue": None}
        if isinstance(value, bool):
            return {"booleanValue": value}
        if isinstance(value, int):
            return self._integer(value)
        if isinstance(value, float):
            return self._double(value)
        if isinstance(value, datetime):
            return self._timestamp(value)
        return self._string(str(value))

    @staticmethod
    def _string(value: str) -> dict[str, str]:
        return {"stringValue": value}

    @staticmethod
    def _integer(value: int) -> dict[str, str]:
        return {"integerValue": str(value)}

    @staticmethod
    def _double(value: float) -> dict[str, float]:
        return {"doubleValue": value}

    @staticmethod
    def _timestamp(value: datetime) -> dict[str, str]:
        return {"timestampValue": value.astimezone(UTC).isoformat()}

    def _document_url(self, path: str) -> str:
        return f"https://firestore.googleapis.com/v1/{self._document_name(path)}"

    def _document_name(self, path: str) -> str:
        return f"projects/{self.client.config.project_id}/databases/(default)/documents/{path}"

    def _commit_url(self) -> str:
        return (
            f"https://firestore.googleapis.com/v1/projects/{self.client.config.project_id}"
            "/databases/(default)/documents:commit"
        )

    def _query_by_status(self, owner_uid: str, statuses: list[str]) -> list[CloudTask]:
        identity = self.client.authenticate()
        owner_path = f"users/{quote(owner_uid, safe='')}"
        payload = {
            "structuredQuery": {
                "from": [{"collectionId": "generationRequests"}],
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": "status"},
                        "op": "IN",
                        "value": {
                            "arrayValue": {
                                "values": [self._string(status) for status in statuses],
                            }
                        },
                    }
                },
            }
        }
        _, value = self.client._json_request(
            "查询待生成任务",
            "POST",
            f"{self._document_url(owner_path)}:runQuery",
            payload=payload,
            id_token=identity.id_token,
        )
        rows = value if isinstance(value, list) else []
        return [
            self._task(owner_uid, row["document"])
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("document"), dict)
        ]

    def _query_deletions_by_status(
        self,
        owner_uid: str,
        statuses: list[str],
    ) -> list[CloudDeletion]:
        identity = self.client.authenticate()
        owner_path = f"users/{quote(owner_uid, safe='')}"
        payload = {
            "structuredQuery": {
                "from": [{"collectionId": "audioDeletionRequests"}],
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": "status"},
                        "op": "IN",
                        "value": {
                            "arrayValue": {
                                "values": [self._string(status) for status in statuses],
                            }
                        },
                    }
                },
            }
        }
        _, value = self.client._json_request(
            "查询待删除音频",
            "POST",
            f"{self._document_url(owner_path)}:runQuery",
            payload=payload,
            id_token=identity.id_token,
        )
        rows = value if isinstance(value, list) else []
        return [
            self._deletion(owner_uid, row["document"])
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("document"), dict)
        ]


def fields_of_cloud_task() -> tuple[Any, ...]:
    return fields(CloudTask)
