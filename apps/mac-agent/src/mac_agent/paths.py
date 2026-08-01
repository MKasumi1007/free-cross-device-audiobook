from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "米兰读书"
LEGACY_DATA_DIRECTORY = "听见书页"
APP_VERSION = "0.5.3"
AGENT_PORT = 17832
DEFAULT_QWEN_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
DEFAULT_MLX_MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"


def data_root() -> Path:
    configured = os.environ.get("AUDIOBOOK_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    # Keep the original directory so a display-name change never hides user data.
    return Path.home() / "Library/Application Support" / LEGACY_DATA_DIRECTORY


def agent_python() -> Path:
    return data_root() / "agent-runtime/bin/python"


def qwen_python() -> Path:
    configured = os.environ.get("AUDIOBOOK_QWEN_PYTHON")
    if configured:
        # Keep the venv entry point intact. Resolving this symlink selects the
        # managed base interpreter and silently drops the venv's site-packages.
        return Path(configured).expanduser().absolute()
    return data_root() / "qwen-runtime/bin/python"


def mlx_python() -> Path:
    configured = os.environ.get("AUDIOBOOK_MLX_PYTHON")
    if configured:
        return Path(configured).expanduser().absolute()
    return data_root() / "mlx-runtime/bin/python"


def models_root() -> Path:
    return data_root() / "models/huggingface"


def logs_root() -> Path:
    return data_root() / "logs"


def state_root() -> Path:
    return data_root() / "state"
