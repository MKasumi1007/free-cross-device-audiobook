from __future__ import annotations

import base64
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .firebase_rest import FirebaseRestClient
from .generation import GenerationError, PublishedAsset
from .error_reporting import reporter


PRIVATE_PART_BYTES = 512 * 1024
PRIVATE_ASSET_LIMIT_BYTES = 32 * 1024 * 1024
PRIVATE_STORAGE_LIMIT_BYTES = 700 * 1024 * 1024


class FirestorePrivateAssetPublisher:
    """Stores owner-only assets in small Firestore documents on the Spark plan."""

    storage_mode = "PRIVATE_FIRESTORE"

    def __init__(
        self,
        client: FirebaseRestClient,
        owner_uid: str,
        *,
        task_id: str = "",
        storage_limit_bytes: int = PRIVATE_STORAGE_LIMIT_BYTES,
    ) -> None:
        self.client = client
        self.owner_uid = owner_uid
        self.task_id = task_id
        self.storage_limit_bytes = storage_limit_bytes
        self._created_keys: dict[int, str] = {}
        self._usage_bytes: int | None = None

    def publish(self, book_id: str, path: Path, asset_name: str) -> PublishedAsset:
        byte_size = path.stat().st_size
        if byte_size <= 0:
            raise GenerationError("PRIVATE_ASSET_EMPTY", "私有文件没有可上传的内容。")
        if byte_size > PRIVATE_ASSET_LIMIT_BYTES:
            raise GenerationError(
                "PRIVATE_ASSET_TOO_LARGE",
                "单个私有文件超过 32 MB，已停止上传，不会启用付费存储。",
            )
        digest = self._hash_file(path)
        asset_key = sha256(
            f"{book_id}:{asset_name}:{digest}".encode("utf-8")
        ).hexdigest()
        existing = self._get_asset(asset_key)
        if (
            existing
            and existing.get("status") == "READY"
            and existing.get("sha256") == digest
            and int(existing.get("byte_size") or 0) == byte_size
        ):
            return self._published(asset_key, asset_name, byte_size, digest, existing, False)
        if existing:
            self.delete_private(asset_key)

        usage = self._private_usage_bytes()
        if usage + byte_size > self.storage_limit_bytes:
            raise GenerationError(
                "PRIVATE_STORAGE_LIMIT",
                "私有音频区已接近免费上限，请先在“音频空间”删除旧音频。",
            )

        part_count = max(1, (byte_size + PRIVATE_PART_BYTES - 1) // PRIVATE_PART_BYTES)
        metadata = {
            "owner_uid": self._string(self.owner_uid),
            "asset_key": self._string(asset_key),
            "book_id": self._string(book_id),
            "task_id": self._string(self.task_id),
            "asset_name": self._string(asset_name),
            "kind": self._string(self._kind(asset_name)),
            "status": self._string("UPLOADING"),
            "content_type": self._string(self._content_type(asset_name)),
            "byte_size": self._integer(byte_size),
            "part_count": self._integer(part_count),
            "sha256": self._string(digest),
        }
        try:
            update_time = self._write_document(
                self._asset_path(asset_key),
                metadata,
                exists=False,
                created=True,
            )
            with path.open("rb") as handle:
                for index in range(part_count):
                    block = handle.read(PRIVATE_PART_BYTES)
                    self._write_document(
                        f"{self._asset_path(asset_key)}/parts/{index:04d}",
                        {
                            "owner_uid": self._string(self.owner_uid),
                            "asset_key": self._string(asset_key),
                            "part_index": self._integer(index),
                            "byte_size": self._integer(len(block)),
                            "payload": {"bytesValue": base64.b64encode(block).decode("ascii")},
                        },
                        exists=False,
                        created=True,
                    )
            self._write_document(
                self._asset_path(asset_key),
                {"status": self._string("READY")},
                update_time=update_time,
            )
            verified = self._get_asset(asset_key)
            if not verified or verified.get("status") != "READY":
                raise GenerationError("PRIVATE_UPLOAD_VERIFY_FAILED", "私有文件上传后校验失败。")
        except Exception as error:
            try:
                self.delete_private(asset_key, part_count=part_count)
            except Exception as rollback_error:
                reporter.record(
                    "private_assets.rollback_upload",
                    rollback_error,
                    code=getattr(rollback_error, "code", "PRIVATE_ROLLBACK_FAILED"),
                    details={"asset_key": asset_key, "original_error": repr(error)},
                )
            if isinstance(error, GenerationError):
                raise
            raise GenerationError(
                "PRIVATE_STORAGE_UNAVAILABLE",
                "账号私有区暂时无法访问，稍后会自动重试。",
            ) from error

        self._usage_bytes = usage + byte_size
        asset = self._published(
            asset_key,
            asset_name,
            byte_size,
            digest,
            {"part_count": part_count, "content_type": self._content_type(asset_name)},
            True,
        )
        self._created_keys[asset.asset_id] = asset_key
        return asset

    def delete(self, _book_id: str, asset_id: int) -> None:
        asset_key = self._created_keys.pop(asset_id, "")
        if asset_key:
            self.delete_private(asset_key)

    def delete_private(self, asset_key: str, *, part_count: int | None = None) -> None:
        try:
            existing = self._get_asset(asset_key)
            count = part_count
            if count is None:
                count = int((existing or {}).get("part_count") or 0)
            for index in range(max(0, count)):
                self._delete_document(f"{self._asset_path(asset_key)}/parts/{index:04d}")
            self._delete_document(self._asset_path(asset_key))
            if self._get_asset(asset_key) is not None:
                raise GenerationError(
                    "PRIVATE_DELETE_VERIFY_FAILED",
                    "私有文件仍然存在，稍后会重试删除。",
                )
            if existing and self._usage_bytes is not None:
                self._usage_bytes = max(
                    0,
                    self._usage_bytes - int(existing.get("byte_size") or 0),
                )
        except Exception as error:
            if isinstance(error, GenerationError):
                raise
            raise GenerationError(
                "PRIVATE_DELETE_FAILED",
                "私有音频暂时无法删除，稍后会自动重试。",
            ) from error

    def _private_usage_bytes(self) -> int:
        if self._usage_bytes is not None:
            return self._usage_bytes
        identity = self.client.authenticate()
        owner_path = f"users/{quote(self.owner_uid, safe='')}"
        payload = {"structuredQuery": {"from": [{"collectionId": "privateAssets"}]}}
        _, value = self.client._json_request(
            "核对私有空间",
            "POST",
            f"{self._document_url(owner_path)}:runQuery",
            payload=payload,
            id_token=identity.id_token,
        )
        rows = value if isinstance(value, list) else []
        self._usage_bytes = sum(
            int(fields.get("byte_size") or 0)
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("document"), dict)
            and (fields := self._fields(row["document"])).get("status") == "READY"
        )
        return self._usage_bytes

    def _get_asset(self, asset_key: str) -> dict[str, Any] | None:
        identity = self.client.authenticate()
        _, value = self.client._json_request(
            "读取私有文件信息",
            "GET",
            self._document_url(self._asset_path(asset_key)),
            id_token=identity.id_token,
            allowed_statuses=frozenset({200, 404}),
        )
        return self._fields(value) if value.get("name") else None

    def _write_document(
        self,
        path: str,
        fields: dict[str, dict[str, Any]],
        *,
        exists: bool | None = None,
        update_time: str = "",
        created: bool = False,
    ) -> str:
        identity = self.client.authenticate()
        write: dict[str, Any] = {
            "update": {"name": self._document_name(path), "fields": fields},
            "updateMask": {"fieldPaths": list(fields)},
            "updateTransforms": [
                {"fieldPath": "updated_at", "setToServerValue": "REQUEST_TIME"}
            ],
        }
        if created:
            write["updateTransforms"].append(
                {"fieldPath": "created_at", "setToServerValue": "REQUEST_TIME"}
            )
        if exists is not None:
            write["currentDocument"] = {"exists": exists}
        elif update_time:
            write["currentDocument"] = {"updateTime": update_time}
        _, value = self.client._json_request(
            "保存私有文件",
            "POST",
            self._commit_url(),
            payload={"writes": [write]},
            id_token=identity.id_token,
        )
        results = value.get("writeResults", [])
        return str(results[0].get("updateTime") or "") if results else ""

    def _delete_document(self, path: str) -> None:
        identity = self.client.authenticate()
        self.client._json_request(
            "删除私有文件",
            "DELETE",
            self._document_url(path),
            id_token=identity.id_token,
            allowed_statuses=frozenset({200, 404}),
        )

    def _published(
        self,
        asset_key: str,
        name: str,
        byte_size: int,
        digest: str,
        fields: dict[str, Any],
        created: bool,
    ) -> PublishedAsset:
        return PublishedAsset(
            asset_id=int(asset_key[:15], 16),
            name=name,
            url="",
            byte_size=byte_size,
            sha256=digest,
            created=created,
            storage_mode=self.storage_mode,
            private_key=asset_key,
            part_count=int(fields.get("part_count") or 0),
            content_type=str(fields.get("content_type") or self._content_type(name)),
        )

    def _asset_path(self, asset_key: str) -> str:
        return f"users/{quote(self.owner_uid, safe='')}/privateAssets/{quote(asset_key, safe='')}"

    def _document_url(self, path: str) -> str:
        return f"https://firestore.googleapis.com/v1/{self._document_name(path)}"

    def _document_name(self, path: str) -> str:
        return f"projects/{self.client.config.project_id}/databases/(default)/documents/{path}"

    def _commit_url(self) -> str:
        return (
            f"https://firestore.googleapis.com/v1/projects/{self.client.config.project_id}"
            "/databases/(default)/documents:commit"
        )

    @staticmethod
    def _fields(document: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in document.get("fields", {}).items():
            if "stringValue" in value:
                result[name] = value["stringValue"]
            elif "integerValue" in value:
                result[name] = int(value["integerValue"])
            elif "nullValue" in value:
                result[name] = None
        return result

    @staticmethod
    def _kind(name: str) -> str:
        if name.startswith("book-"):
            return "BOOK_TEXT"
        if name.startswith("timeline-"):
            return "TIMELINE"
        return "AUDIO"

    @staticmethod
    def _content_type(name: str) -> str:
        return "application/gzip" if name.endswith(".json.gz") else "audio/mp4"

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _string(value: str) -> dict[str, str]:
        return {"stringValue": value}

    @staticmethod
    def _integer(value: int) -> dict[str, str]:
        return {"integerValue": str(value)}
