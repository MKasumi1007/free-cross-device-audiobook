from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


LABEL = "io.github.mkasumi1007.audiobook-mac-agent"


def install_launch_agent(*, python_path: Path | None = None) -> Path:
    data_root = Path.home() / "Library/Application Support/听见书页"
    installed_runtime = data_root / "agent-runtime/bin/python"
    python = (
        python_path
        or (installed_runtime if installed_runtime.is_file() else Path(sys.prefix) / "bin/python")
    ).absolute()
    logs = data_root / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    agents = Path.home() / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    target = agents / f"{LABEL}.plist"
    public_config = Path.cwd() / "config/firebase-public-config.json"
    if public_config.is_file():
        installed_config = data_root / "firebase-public-config.json"
        shutil.copy2(public_config, installed_config)
        installed_config.chmod(0o600)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "mac_agent.main"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "agent.log"),
        "StandardErrorPath": str(logs / "agent-error.log"),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "AUDIOBOOK_QWEN_PYTHON": str(
                data_root / "qwen-runtime/bin/python"
            ),
        },
    }
    temporary = target.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return target


def reload_launch_agent(path: Path) -> None:
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Mac 后台启动项安装失败。")
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> None:
    path = install_launch_agent()
    reload_launch_agent(path)
    print("听书工具已设置为登录后自动启动。")
