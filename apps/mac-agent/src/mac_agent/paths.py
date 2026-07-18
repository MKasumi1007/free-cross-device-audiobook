from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "听见书页"
APP_VERSION = "0.2.0"
AGENT_PORT = 17832
DEFAULT_QWEN_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"


def data_root() -> Path:
    configured = os.environ.get("AUDIOBOOK_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / "Library/Application Support" / APP_NAME


def agent_python() -> Path:
    return data_root() / "agent-runtime/bin/python"


def qwen_python() -> Path:
    configured = os.environ.get("AUDIOBOOK_QWEN_PYTHON")
    if configured:
        # Keep the venv entry point intact. Resolving this symlink selects the
        # managed base interpreter and silently drops the venv's site-packages.
        return Path(configured).expanduser().absolute()
    return data_root() / "qwen-runtime/bin/python"


def models_root() -> Path:
    return data_root() / "models/huggingface"


def logs_root() -> Path:
    return data_root() / "logs"


def state_root() -> Path:
    return data_root() / "state"
