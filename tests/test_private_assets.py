from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mac_agent.firebase_rest import Identity
from mac_agent.generation import GenerationError
from mac_agent.private_assets import FirestorePrivateAssetPublisher, PRIVATE_PART_BYTES


class InMemoryFirestoreClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(project_id="demo")
        self.identity = Identity("token", "refresh", "worker-a")
        self.documents: dict[str, dict[str, Any]] = {}
        self.clock = 0

    def authenticate(self) -> Identity:
        return self.identity

    def _json_request(
        self,
        _label: str,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        id_token: str | None = None,
        allowed_statuses: frozenset[int] = frozenset({200}),
        **_kwargs: Any,
    ) -> tuple[int, Any]:
        assert id_token == "token"
        marker = "/documents/"
        if method == "POST" and url.endswith(":runQuery"):
            rows = [
                {"document": self._document(path, fields)}
                for path, fields in self.documents.items()
                if path.startswith("users/owner-a/privateAssets/")
                and "/parts/" not in path
            ]
            return 200, rows
        if method == "POST" and url.endswith("documents:commit"):
            assert payload is not None
            results = []
            for write in payload["writes"]:
                update = write["update"]
                path = str(update["name"]).split(marker, 1)[1]
                fields = dict(self.documents.get(path, {}))
                fields.update(update.get("fields", {}))
                for transform in write.get("updateTransforms", []):
                    fields[transform["fieldPath"]] = {"timestampValue": "2026-07-17T00:00:00Z"}
                self.documents[path] = fields
                self.clock += 1
                results.append({"updateTime": f"2026-07-17T00:00:{self.clock:02d}Z"})
            return 200, {"writeResults": results}
        path = url.split(marker, 1)[1]
        if method == "GET":
            fields = self.documents.get(path)
            if fields is None:
                assert 404 in allowed_statuses
                return 404, {}
            return 200, self._document(path, fields)
        if method == "DELETE":
            self.documents.pop(path, None)
            return 200, {}
        raise AssertionError(f"unexpected request: {method} {url}")

    @staticmethod
    def _document(path: str, fields: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": f"projects/demo/databases/(default)/documents/{path}",
            "fields": fields,
            "updateTime": "2026-07-17T00:00:00Z",
        }


def test_private_asset_is_chunked_reused_and_deleted(tmp_path: Path) -> None:
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"a" * (PRIVATE_PART_BYTES + 123))
    client = InMemoryFirestoreClient()
    publisher = FirestorePrivateAssetPublisher(client, "owner-a", task_id="task-a")  # type: ignore[arg-type]

    first = publisher.publish("book-a", source, "audio-chunk-a.m4a")
    second = publisher.publish("book-a", source, "audio-chunk-a.m4a")

    assert first.storage_mode == "PRIVATE_FIRESTORE"
    assert first.url == ""
    assert first.part_count == 2
    assert second.created is False
    part_paths = sorted(path for path in client.documents if "/parts/" in path)
    assert len(part_paths) == 2
    sizes = [
        len(base64.b64decode(client.documents[path]["payload"]["bytesValue"]))
        for path in part_paths
    ]
    assert sizes == [PRIVATE_PART_BYTES, 123]

    publisher.delete_private(first.private_key)
    assert not any(first.private_key in path for path in client.documents)


def test_private_asset_stops_before_free_storage_limit(tmp_path: Path) -> None:
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"private audio")
    client = InMemoryFirestoreClient()
    publisher = FirestorePrivateAssetPublisher(  # type: ignore[arg-type]
        client,
        "owner-a",
        storage_limit_bytes=4,
    )

    with pytest.raises(GenerationError) as error:
        publisher.publish("book-a", source, "audio-chunk-a.m4a")

    assert error.value.code == "PRIVATE_STORAGE_LIMIT"
    assert client.documents == {}
