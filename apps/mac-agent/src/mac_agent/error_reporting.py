from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import DEFAULT_QWEN_MODEL, data_root, logs_root, qwen_python
from .resources import ResourcePolicy


class AgentOperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.details = details or {}


class LocalErrorReporter:
    """Writes private structured diagnostics without exposing them to the web app."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or logs_root() / "diagnostics.jsonl"
        self.latest_path = self.path.parent / "latest-error.json"
        self._lock = threading.Lock()

    def record(
        self,
        operation: str,
        error: BaseException,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_code = code or getattr(error, "code", "UNEXPECTED_ERROR")
        private_details = dict(getattr(error, "details", {}) or {})
        private_details.update(details or {})
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "operation": operation,
            "error_type": type(error).__name__,
            "error_code": resolved_code,
            "message": str(error),
            "exception_repr": repr(error),
            "traceback": "".join(traceback.format_exception(error)),
            "details": private_details,
            "runtime": self._runtime_snapshot(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            self.path.chmod(0o600)
            public = {
                "timestamp": payload["timestamp"],
                "operation": operation,
                "error_code": resolved_code,
                "message": self._public_message(error),
            }
            temporary = self.latest_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(public, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            os.replace(temporary, self.latest_path)
        return payload

    def latest_public(self) -> dict[str, str] | None:
        try:
            value = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return {
            "timestamp": str(value.get("timestamp") or ""),
            "operation": str(value.get("operation") or ""),
            "error_code": str(value.get("error_code") or ""),
            "message": str(value.get("message") or ""),
        }

    @staticmethod
    def _public_message(error: BaseException) -> str:
        if isinstance(error, AgentOperationError):
            return error.user_message
        message = str(error).strip()
        return message if message and len(message) <= 240 else "操作没有完成，请在系统状态中查看修复建议。"

    @staticmethod
    def _runtime_snapshot() -> dict[str, Any]:
        root = data_root()
        policy = ResourcePolicy(root / "generation-settings.json")
        return {
            "pid": os.getpid(),
            "python": sys.executable,
            "qwen_python": str(qwen_python()),
            "model": os.environ.get("AUDIOBOOK_QWEN_MODEL", DEFAULT_QWEN_MODEL),
            "ffmpeg": shutil.which("ffmpeg"),
            "available_memory_bytes": policy.available_memory_bytes(),
            "disk_free_bytes": shutil.disk_usage(root.parent if root.exists() else Path.home()).free,
        }


def completed_process_details(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "command": [str(part) for part in result.args] if isinstance(result.args, list) else str(result.args),
        "exit_code": result.returncode,
        "stdout": (result.stdout or "")[-12_000:],
        "stderr": (result.stderr or "")[-12_000:],
    }


reporter = LocalErrorReporter()
