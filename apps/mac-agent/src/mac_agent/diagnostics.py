from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .error_reporting import LocalErrorReporter, reporter
from .launchd import LABEL
from .paths import (
    AGENT_PORT,
    APP_VERSION,
    DEFAULT_MLX_MODEL,
    DEFAULT_QWEN_MODEL,
    data_root,
    logs_root,
    mlx_python,
    models_root,
    qwen_python,
    state_root,
)
from .resources import ResourcePolicy


DiagnosticLevel = Literal["ok", "warning", "failed"]


@dataclass(frozen=True)
class DiagnosticItem:
    key: str
    label: str
    status: DiagnosticLevel
    detail: str
    suggestion: str = ""
    repair_action: str = ""


class SystemDiagnostics:
    def __init__(self, *, error_reporter: LocalErrorReporter = reporter) -> None:
        self.root = data_root()
        self.error_reporter = error_reporter

    def report(self, *, cloud_configured: bool, paired: bool | None, worker: Any = None) -> dict[str, Any]:
        backend = os.environ.get("AUDIOBOOK_TTS_BACKEND", "qwen").strip().lower()
        backend_items = (
            [self._mlx_python(), *self._mlx_imports()]
            if backend == "mlx"
            else [self._qwen_python(), *self._qwen_imports()]
        )
        items = [
            self._launch_agent(),
            self._agent_python(),
            DiagnosticItem(
                key="tts_backend",
                label="声音引擎",
                status="ok" if backend in {"mlx", "qwen"} else "failed",
                detail="MLX 4 位加速引擎（双段并行）。" if backend == "mlx" else "Qwen PyTorch 兼容引擎。",
                suggestion="点击自动修复恢复声音引擎设置。" if backend not in {"mlx", "qwen"} else "",
                repair_action="runtime" if backend not in {"mlx", "qwen"} else "",
            ),
            *backend_items,
            self._binary("ffmpeg", "FFmpeg", "FFMPEG_MISSING"),
            self._model_files(),
            self._model_self_test(),
            self._mps(),
            self._memory(),
            self._disk(),
            DiagnosticItem(
                key="firebase_config",
                label="Firebase 配置",
                status="ok" if cloud_configured else "failed",
                detail="已载入公开客户端配置。" if cloud_configured else "没有找到 Firebase 公开配置。",
                suggestion="重新运行安装器以恢复公开配置。" if not cloud_configured else "",
                repair_action="runtime" if not cloud_configured else "",
            ),
            DiagnosticItem(
                key="pairing",
                label="当前 Mac 配对",
                status="ok" if paired else ("warning" if paired is False else "warning"),
                detail="这台 Mac 已与登录账号配对。" if paired else "尚未确认这台 Mac 的账号配对状态。",
                suggestion="登录网页后点击“连接这台 Mac”。" if not paired else "",
            ),
        ]
        worker_status = worker.status() if worker else {"state": "NOT_CONFIGURED", "error": "", "model_loaded": False}
        return {
            "schema_version": 1,
            "checked_at": datetime.now(UTC).isoformat(),
            "agent_version": APP_VERSION,
            "agent_port": AGENT_PORT,
            "data_root": str(self.root),
            "log_path": str(logs_root() / "diagnostics.jsonl"),
            "worker": worker_status,
            "recent_error": self.error_reporter.latest_public(),
            "items": [asdict(item) for item in items],
        }

    def start_repair(self, action: str) -> dict[str, str]:
        allowed = {"runtime", "qwen", "mlx", "model", "launch_agent"}
        if action not in allowed:
            raise ValueError("不支持的自动修复操作。")
        repair = self.root / "installer/repair.sh"
        if not repair.is_file():
            raise FileNotFoundError("自动修复组件不存在，请重新下载并双击安装器。")
        log = logs_root() / "repair.log"
        log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = log.open("ab")
        subprocess.Popen(  # noqa: S603
            ["/bin/bash", str(repair), action],
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        handle.close()
        return {"status": "started", "action": action, "message": "自动修复已开始，完成后请重新检查。"}

    def _launch_agent(self) -> DiagnosticItem:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True,
            text=True,
            check=False,
        )
        running = result.returncode == 0 and "state = running" in result.stdout
        return DiagnosticItem(
            key="launch_agent",
            label="登录自动启动",
            status="ok" if running else "failed",
            detail="LaunchAgent 正在运行。" if running else "LaunchAgent 没有运行。",
            suggestion="点击自动修复重新注册并启动后台服务。" if not running else "",
            repair_action="launch_agent" if not running else "",
        )

    def _agent_python(self) -> DiagnosticItem:
        prefix = Path(sys.prefix)
        inside = self._is_within(prefix, self.root / "agent-runtime")
        return DiagnosticItem(
            key="agent_python",
            label="Agent Python",
            status="ok" if inside else "warning",
            detail=f"{sys.executable}（venv: {prefix}）",
            suggestion="正式服务应使用 Application Support 中的独立运行时。" if not inside else "",
            repair_action="runtime" if not inside else "",
        )

    def _qwen_python(self) -> DiagnosticItem:
        path = qwen_python()
        valid = path.is_file() and os.access(path, os.X_OK) and self._is_within(path, self.root)
        return DiagnosticItem(
            key="qwen_python",
            label="Qwen Python",
            status="ok" if valid else "failed",
            detail=str(path),
            suggestion="点击自动修复建立独立 Qwen 运行环境。" if not valid else "",
            repair_action="qwen" if not valid else "",
        )

    def _qwen_imports(self) -> list[DiagnosticItem]:
        path = qwen_python()
        names = (("torch", "torch"), ("qwen_tts", "qwen-tts"), ("soundfile", "soundfile"))
        if not path.is_file():
            return [
                DiagnosticItem(
                    key=f"import_{module}",
                    label=f"导入 {label}",
                    status="failed",
                    detail="Qwen 运行环境不存在。",
                    suggestion="点击自动修复安装 Qwen 依赖。",
                    repair_action="qwen",
                )
                for module, label in names
            ]
        script = (
            "import importlib,json;"
            "names=['torch','qwen_tts','soundfile'];"
            "out={};"
            "\nfor name in names:\n"
            " try:\n  importlib.import_module(name); out[name]={'ok':True}\n"
            " except Exception as exc:\n  out[name]={'ok':False,'error':type(exc).__name__+': '+str(exc)}\n"
            "print(json.dumps(out))"
        )
        try:
            result = subprocess.run(
                [str(path), "-c", script],
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
                env=self._qwen_env(),
            )
            value = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, IndexError) as error:
            self.error_reporter.record("diagnostics.qwen_imports", error, code="QWEN_IMPORT_CHECK_FAILED")
            value = {}
        items: list[DiagnosticItem] = []
        for module, label in names:
            record = value.get(module, {}) if isinstance(value, dict) else {}
            ok = bool(record.get("ok"))
            items.append(DiagnosticItem(
                key=f"import_{module}",
                label=f"导入 {label}",
                status="ok" if ok else "failed",
                detail="依赖可以正常导入。" if ok else str(record.get("error") or "依赖导入失败。"),
                suggestion="点击自动修复重建 Qwen 环境。" if not ok else "",
                repair_action="qwen" if not ok else "",
            ))
        return items

    def _mlx_python(self) -> DiagnosticItem:
        path = mlx_python()
        valid = path.is_file() and os.access(path, os.X_OK) and self._is_within(path, self.root)
        return DiagnosticItem(
            key="mlx_python",
            label="MLX Python",
            status="ok" if valid else "failed",
            detail=str(path),
            suggestion="点击自动修复建立独立 MLX 运行环境。" if not valid else "",
            repair_action="mlx" if not valid else "",
        )

    def _mlx_imports(self) -> list[DiagnosticItem]:
        path = mlx_python()
        names = (("mlx", "MLX"), ("mlx_audio", "MLX-Audio"), ("soundfile", "soundfile"))
        if not path.is_file():
            return [
                DiagnosticItem(
                    key=f"import_{module}",
                    label=f"导入 {label}",
                    status="failed",
                    detail="MLX 运行环境不存在。",
                    suggestion="点击自动修复安装 MLX 依赖。",
                    repair_action="mlx",
                )
                for module, label in names
            ]
        script = (
            "import importlib,json;"
            "names=['mlx','mlx_audio','soundfile'];"
            "out={};"
            "\nfor name in names:\n"
            " try:\n  importlib.import_module(name); out[name]={'ok':True}\n"
            " except Exception as exc:\n  out[name]={'ok':False,'error':type(exc).__name__+': '+str(exc)}\n"
            "print(json.dumps(out))"
        )
        try:
            result = subprocess.run(
                [str(path), "-c", script],
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
                env=self._mlx_env(),
            )
            value = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, IndexError) as error:
            self.error_reporter.record(
                "diagnostics.mlx_imports",
                error,
                code="MLX_IMPORT_CHECK_FAILED",
            )
            value = {}
        items: list[DiagnosticItem] = []
        for module, label in names:
            record = value.get(module, {}) if isinstance(value, dict) else {}
            ok = bool(record.get("ok"))
            items.append(DiagnosticItem(
                key=f"import_{module}",
                label=f"导入 {label}",
                status="ok" if ok else "failed",
                detail="依赖可以正常导入。" if ok else str(record.get("error") or "依赖导入失败。"),
                suggestion="点击自动修复重建 MLX 环境。" if not ok else "",
                repair_action="mlx" if not ok else "",
            ))
        return items

    @staticmethod
    def _binary(name: str, label: str, _code: str) -> DiagnosticItem:
        path = shutil.which(name)
        return DiagnosticItem(
            key=name,
            label=label,
            status="ok" if path else "failed",
            detail=path or f"没有找到 {label}。",
            suggestion="重新运行安装器；安装器会检测并记录音频工具路径。" if not path else "",
            repair_action="runtime" if not path else "",
        )

    def _model_files(self) -> DiagnosticItem:
        mlx_backend = os.environ.get("AUDIOBOOK_TTS_BACKEND", "qwen").strip().lower() == "mlx"
        model = DEFAULT_MLX_MODEL if mlx_backend else DEFAULT_QWEN_MODEL
        repository = (
            "models--mlx-community--Qwen3-TTS-12Hz-0.6B-Base-4bit"
            if mlx_backend
            else "models--Qwen--Qwen3-TTS-12Hz-0.6B-Base"
        )
        cache = models_root() / f"hub/{repository}/snapshots"
        configs = list(cache.glob("*/config.json")) if cache.exists() else []
        return DiagnosticItem(
            key="model_files",
            label="声音模型文件",
            status="ok" if configs else "failed",
            detail=str(configs[0].parent) if configs else f"{model} 尚未下载到正式模型目录。",
            suggestion="点击自动修复下载免费模型。" if not configs else "",
            repair_action="mlx" if mlx_backend and not configs else "model" if not configs else "",
        )

    def _model_self_test(self) -> DiagnosticItem:
        mlx_backend = os.environ.get("AUDIOBOOK_TTS_BACKEND", "qwen").strip().lower() == "mlx"
        expected_model = DEFAULT_MLX_MODEL if mlx_backend else DEFAULT_QWEN_MODEL
        path = state_root() / (
            "mlx-model-self-test.json" if mlx_backend else "model-self-test.json"
        )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            ok = value.get("status") == "ok" and value.get("model") == expected_model
        except (OSError, json.JSONDecodeError, TypeError):
            value = {}
            ok = False
        return DiagnosticItem(
            key="model_self_test",
            label="真实模型生成自检",
            status="ok" if ok else "warning",
            detail=(
                f"{value.get('checked_at')}："
                f"{'MLX' if mlx_backend else 'MPS'} 生成 WAV 并编码 M4A 成功。"
                if ok else "尚无正式运行目录中的真实生成证据。"
            ),
            suggestion="点击自动修复执行模型加载和短中文生成测试。" if not ok else "",
            repair_action="mlx" if mlx_backend and not ok else "model" if not ok else "",
        )

    def _mps(self) -> DiagnosticItem:
        if os.environ.get("AUDIOBOOK_TTS_BACKEND", "qwen").strip().lower() == "mlx":
            path = mlx_python()
            if not path.is_file():
                return DiagnosticItem(
                    "mps",
                    "Apple GPU",
                    "failed",
                    "无法检查 MLX Metal。",
                    "先修复 MLX 环境。",
                    "mlx",
                )
            result = subprocess.run(
                [str(path), "-c", "import mlx.core as mx; print(int(mx.metal.is_available()))"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
                env=self._mlx_env(),
            )
            available = result.returncode == 0 and result.stdout.strip().endswith("1")
            return DiagnosticItem(
                key="mps",
                label="Apple GPU",
                status="ok" if available else "failed",
                detail="MLX Metal 可用。" if available else "MLX Metal 不可用。",
                suggestion="确认使用 Apple Silicon 和受支持的 macOS。" if not available else "",
                repair_action="mlx" if not available else "",
            )
        path = qwen_python()
        if not path.is_file():
            return DiagnosticItem("mps", "Apple MPS", "failed", "无法检查 MPS。", "先修复 Qwen 环境。", "qwen")
        result = subprocess.run(
            [str(path), "-c", "import torch; print(int(torch.backends.mps.is_available()))"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=self._qwen_env(),
        )
        available = result.returncode == 0 and result.stdout.strip().endswith("1")
        return DiagnosticItem(
            key="mps",
            label="Apple MPS",
            status="ok" if available else "warning",
            detail="MPS 可用，优先使用 Apple GPU。" if available else "MPS 不可用，将降级到 CPU；速度会明显变慢。",
            suggestion="确认使用 Apple Silicon 和兼容的 torch；CPU 降级仍可工作。" if not available else "",
        )

    def _memory(self) -> DiagnosticItem:
        available = ResourcePolicy.available_memory_bytes()
        ok = available is not None and available >= 2 * 1024**3
        return DiagnosticItem(
            key="memory",
            label="可用内存",
            status="ok" if ok else "warning",
            detail=f"约 {(available or 0) / 1024**3:.1f} GiB 可用。",
            suggestion="关闭占用内存较大的应用；低于 2 GiB 时生成会暂停。" if not ok else "",
        )

    def _disk(self) -> DiagnosticItem:
        anchor = self.root if self.root.exists() else Path.home()
        free = shutil.disk_usage(anchor).free
        ok = free >= 6 * 1024**3
        return DiagnosticItem(
            key="disk",
            label="剩余磁盘",
            status="ok" if ok else "warning",
            detail=f"约 {free / 1024**3:.1f} GiB 可用。",
            suggestion="建议至少保留 6 GiB；模型下载和安全更新需要临时空间。" if not ok else "",
        )

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            # Do not follow venv interpreter symlinks: doing so changes a
            # correct runtime path into the shared managed Python path.
            path.absolute().relative_to(root.absolute())
            return True
        except ValueError:
            return False

    @staticmethod
    def _qwen_env() -> dict[str, str]:
        return {
            **os.environ,
            "HF_HOME": str(models_root()),
            "AUDIOBOOK_QWEN_MODEL": os.environ.get("AUDIOBOOK_QWEN_MODEL", DEFAULT_QWEN_MODEL),
        }

    @staticmethod
    def _mlx_env() -> dict[str, str]:
        return {
            **os.environ,
            "HF_HOME": str(models_root()),
            "AUDIOBOOK_MLX_MODEL": os.environ.get("AUDIOBOOK_MLX_MODEL", DEFAULT_MLX_MODEL),
        }
