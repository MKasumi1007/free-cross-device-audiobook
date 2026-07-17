from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import unquote, urlparse

from .generation import GenerationError, PublishedAsset
from .release_assets import GitHubReleasePublisher


class GitHubRepositoryAssetPublisher:
    """Publishes browser-readable JSON assets to an isolated public branch."""

    BRANCH = "book-assets"

    def __init__(self, repository: str) -> None:
        self.repository = repository
        self._created_paths: dict[int, str] = {}

    def publish(self, book_id: str, path: Path, asset_name: str) -> PublishedAsset:
        self._ensure_branch()
        local_hash = self._hash_file(path)
        remote_path = f"books/{book_id}/{asset_name}"
        existing = self._metadata(remote_path)
        if existing is not None:
            verified = self._verify(remote_path, existing, local_hash)
            if verified.byte_size != path.stat().st_size:
                raise GenerationError(
                    "REMOTE_ASSET_MISMATCH",
                    "远端同名数据与本机内容不一致。",
                )
            return replace(verified, created=False)

        payload = {
            "message": f"Publish audiobook data for {book_id}",
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "branch": self.BRANCH,
        }
        result = self._api(
            "PUT",
            f"repos/{self.repository}/contents/{quote(remote_path, safe='/')}",
            payload,
        )
        if result.returncode != 0:
            raise self._command_error(
                result,
                "GITHUB_DATA_UPLOAD_FAILED",
                "GitHub 正文或时间轴上传中断，稍后会自动重试。",
            )
        metadata = self._metadata(remote_path)
        if metadata is None:
            raise GenerationError("GITHUB_VERIFY_FAILED", "上传后没有找到远端数据。")
        verified = self._verify(remote_path, metadata, local_hash)
        if verified.byte_size != path.stat().st_size:
            raise GenerationError("REMOTE_ASSET_MISMATCH", "远端数据大小校验失败。")
        self._created_paths[verified.asset_id] = remote_path
        return verified

    def delete(self, _book_id: str, asset_id: int) -> None:
        remote_path = self._created_paths.pop(asset_id, None)
        if remote_path is None:
            return
        metadata = self._metadata(remote_path)
        if metadata is None:
            return
        payload = {
            "message": "Roll back stale audiobook data",
            "sha": str(metadata["sha"]),
            "branch": self.BRANCH,
        }
        result = self._api(
            "DELETE",
            f"repos/{self.repository}/contents/{quote(remote_path, safe='/')}",
            payload,
        )
        if result.returncode != 0 and b"HTTP 404" not in result.stderr:
            raise self._command_error(result, "GITHUB_ROLLBACK_FAILED", "迟到数据未能自动回滚，需要稍后对账。")

    def delete_persisted(self, asset_id: int, url: str) -> None:
        remote_path = self._path_from_url(url)
        metadata = self._metadata(remote_path)
        if metadata is None:
            return
        remote_sha = str(metadata.get("sha") or "")
        if not remote_sha or int(remote_sha[:15], 16) != asset_id:
            raise GenerationError("REMOTE_ASSET_MISMATCH", "时间轴标识与远端文件不一致，已停止删除。")
        result = self._api(
            "DELETE",
            f"repos/{self.repository}/contents/{quote(remote_path, safe='/')}",
            {
                "message": "Delete audiobook timeline after user confirmation",
                "sha": remote_sha,
                "branch": self.BRANCH,
            },
        )
        if result.returncode != 0 and b"HTTP 404" not in result.stderr:
            raise self._command_error(result, "GITHUB_DELETE_FAILED", "GitHub 时间轴暂时无法删除，稍后会自动重试。")
        if self._metadata(remote_path) is not None:
            raise GenerationError("GITHUB_DELETE_VERIFY_FAILED", "远程时间轴仍然存在，稍后会再次核对。")

    def asset_exists(self, asset_id: int, url: str) -> bool:
        remote_path = self._path_from_url(url)
        metadata = self._metadata(remote_path)
        if metadata is None:
            return False
        remote_sha = str(metadata.get("sha") or "")
        return bool(remote_sha) and int(remote_sha[:15], 16) == asset_id

    def list_paths(self, book_id: str) -> list[str]:
        result = self._api(
            "GET",
            f"repos/{self.repository}/git/trees/{self.BRANCH}?recursive=1",
        )
        if result.returncode != 0:
            raise self._command_error(result, "GITHUB_RESPONSE_INVALID", "暂时无法读取远程时间轴清单。")
        value = self._api_json(result)
        prefix = f"books/{book_id}/"
        tree = value.get("tree", [])
        return [
            str(item.get("path"))
            for item in tree
            if isinstance(item, dict)
            and item.get("type") == "blob"
            and str(item.get("path") or "").startswith(prefix)
        ]

    def owns_created_asset(self, asset_id: int) -> bool:
        return asset_id in self._created_paths

    def _ensure_branch(self) -> None:
        branch = self._api("GET", f"repos/{self.repository}/git/ref/heads/{self.BRANCH}")
        if branch.returncode == 0:
            return
        if GitHubReleasePublisher._is_limited(branch.stderr):
            raise self._command_error(branch, "GITHUB_BRANCH_FAILED", "无法读取 GitHub 数据分支。")
        repository = self._api_json(self._api("GET", f"repos/{self.repository}"))
        default_branch = str(repository.get("default_branch") or "main")
        source = self._api_json(
            self._api("GET", f"repos/{self.repository}/git/ref/heads/{default_branch}")
        )
        source_sha = str(source.get("object", {}).get("sha") or "")
        if not source_sha:
            raise GenerationError("GITHUB_BRANCH_FAILED", "无法读取 GitHub 默认分支。")
        created = self._api(
            "POST",
            f"repos/{self.repository}/git/refs",
            {"ref": f"refs/heads/{self.BRANCH}", "sha": source_sha},
        )
        if created.returncode != 0:
            raced = self._api("GET", f"repos/{self.repository}/git/ref/heads/{self.BRANCH}")
            if raced.returncode != 0:
                raise self._command_error(raced, "GITHUB_BRANCH_FAILED", "无法创建 GitHub 数据分支。")

    def _metadata(self, remote_path: str) -> dict[str, Any] | None:
        result = self._api(
            "GET",
            f"repos/{self.repository}/contents/{quote(remote_path, safe='/')}?ref={self.BRANCH}",
        )
        if result.returncode != 0:
            if b"HTTP 404" in result.stderr:
                return None
            raise self._command_error(result, "GITHUB_RESPONSE_INVALID", "无法读取 GitHub 数据资产。")
        return self._api_json(result)

    def _path_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.netloc != "raw.githubusercontent.com":
            raise GenerationError("REMOTE_ASSET_URL_INVALID", "时间轴地址不是受管理的 GitHub 文件。")
        parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
        repository_parts = self.repository.split("/", 1)
        if len(parts) < 5 or parts[:2] != repository_parts or parts[2] != self.BRANCH:
            raise GenerationError("REMOTE_ASSET_URL_INVALID", "时间轴地址不属于当前项目。")
        remote_path = "/".join(parts[3:])
        if not remote_path.startswith("books/"):
            raise GenerationError("REMOTE_ASSET_URL_INVALID", "时间轴地址超出书籍目录。")
        return remote_path

    def _verify(
        self,
        remote_path: str,
        metadata: dict[str, Any],
        expected_hash: str,
    ) -> PublishedAsset:
        remote_sha = str(metadata.get("sha") or "")
        if not remote_sha:
            raise GenerationError("GITHUB_ASSET_ID_INVALID", "GitHub 没有返回数据标识。")
        with tempfile.NamedTemporaryFile(prefix="audiobook-data-verify-", delete=False) as output:
            target = Path(output.name)
            result = subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    self._raw_url(remote_path),
                ],
                stdout=output,
                stderr=subprocess.PIPE,
                check=False,
            )
        try:
            if result.returncode != 0 or self._hash_file(target) != expected_hash:
                raise GenerationError("REMOTE_HASH_MISMATCH", "远端数据 SHA-256 校验失败。")
            asset_id = int(remote_sha[:15], 16)
            return PublishedAsset(
                asset_id=asset_id,
                name=str(metadata.get("name") or Path(remote_path).name),
                url=self._raw_url(remote_path),
                byte_size=target.stat().st_size,
                sha256=expected_hash,
            )
        finally:
            target.unlink(missing_ok=True)

    def _raw_url(self, remote_path: str) -> str:
        repository = quote(self.repository, safe="/")
        path = quote(remote_path, safe="/")
        return f"https://raw.githubusercontent.com/{repository}/{self.BRANCH}/{path}"

    @staticmethod
    def _api(
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["gh", "api", "--method", method, endpoint]
        encoded = None
        if payload is not None:
            command.extend(["--input", "-"])
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return subprocess.run(
            command,
            input=encoded,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _api_json(result: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
        if result.returncode != 0:
            if GitHubReleasePublisher._is_limited(result.stderr):
                raise GenerationError(
                    "GITHUB_LIMITED",
                    "GitHub 暂时限制请求，已自动退避，不会改用付费服务。",
                )
            raise GenerationError("GITHUB_RESPONSE_INVALID", "GitHub 返回读取错误。")
        try:
            value = json.loads(result.stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise GenerationError("GITHUB_RESPONSE_INVALID", "GitHub 返回了无法识别的数据。") from error
        if not isinstance(value, dict):
            raise GenerationError("GITHUB_RESPONSE_INVALID", "GitHub 返回了错误的数据格式。")
        return value

    @staticmethod
    def _command_error(
        result: subprocess.CompletedProcess[bytes],
        code: str,
        message: str,
    ) -> GenerationError:
        if GitHubReleasePublisher._is_limited(result.stderr):
            return GenerationError(
                "GITHUB_LIMITED",
                "GitHub 暂时限制请求，已自动退避，不会改用付费服务。",
            )
        return GenerationError(code, message)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()


class GitHubAudiobookPublisher:
    """Keeps streamable audio in Releases and fetchable JSON in the data branch."""

    def __init__(self, repository: str) -> None:
        self.audio = GitHubReleasePublisher(repository)
        self.data = GitHubRepositoryAssetPublisher(repository)

    def publish(self, book_id: str, path: Path, asset_name: str) -> PublishedAsset:
        if asset_name.endswith(".json.gz"):
            return self.data.publish(book_id, path, asset_name)
        return self.audio.publish(book_id, path, asset_name)

    def delete(self, book_id: str, asset_id: int) -> None:
        if self.data.owns_created_asset(asset_id):
            self.data.delete(book_id, asset_id)
        else:
            self.audio.delete(book_id, asset_id)
