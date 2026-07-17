from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from mac_agent.repository_assets import GitHubRepositoryAssetPublisher


def test_repository_asset_is_verified_reused_and_rollback_safe(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    remote: dict[str, bytes] = {}

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if command[0] == "curl":
            path = unquote(command[-1].split("/book-assets/", 1)[1])
            kwargs["stdout"].write(remote[path])
            return subprocess.CompletedProcess(command, 0, b"", b"")
        endpoint = next(item for item in command if item.startswith("repos/"))
        method = command[command.index("--method") + 1] if "--method" in command else "GET"
        if "/git/ref/heads/book-assets" in endpoint:
            return subprocess.CompletedProcess(command, 0, b'{"object":{"sha":"abc"}}', b"")
        if "/contents/" in endpoint:
            path = unquote(endpoint.split("/contents/", 1)[1].split("?", 1)[0])
            if method == "PUT":
                payload = json.loads(kwargs["input"])
                remote[path] = base64.b64decode(payload["content"])
                return subprocess.CompletedProcess(command, 0, b"{}", b"")
            if method == "DELETE":
                remote.pop(path, None)
                return subprocess.CompletedProcess(command, 0, b"{}", b"")
            if path not in remote:
                return subprocess.CompletedProcess(command, 1, b"", b"HTTP 404")
            content = remote[path]
            blob_sha = hashlib.sha1(
                f"blob {len(content)}\0".encode("ascii") + content,
                usedforsecurity=False,
            ).hexdigest()
            metadata = {"name": Path(path).name, "sha": blob_sha, "size": len(content)}
            return subprocess.CompletedProcess(command, 0, json.dumps(metadata).encode(), b"")
        raise AssertionError(command)

    monkeypatch.setattr("mac_agent.repository_assets.subprocess.run", run)
    source = tmp_path / "timeline.json.gz"
    source.write_bytes(b"project-created timeline")
    publisher = GitHubRepositoryAssetPublisher("owner/repository")

    first = publisher.publish("book-a", source, source.name)
    second = publisher.publish("book-a", source, source.name)

    assert first.created is True
    assert second.created is False
    assert first.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert first.url.startswith("https://raw.githubusercontent.com/owner/repository/book-assets/")
    assert len(remote) == 1

    publisher.delete("book-a", first.asset_id)
    assert remote == {}
