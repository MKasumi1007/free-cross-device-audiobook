from __future__ import annotations

import os
import plistlib
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from .paths import data_root as application_data_root

LABEL = "io.github.mkasumi1007.audiobook-mac-agent"
WATCHDOG_LABEL = "io.github.mkasumi1007.audiobook-mac-agent-watchdog"
AGENT_PORT = 17832


def refresh_installed_runtime(source_root: Path | None = None) -> Path | None:
    """Developer-only refresh helper; production updates use the packaged installer."""
    if source_root is None:
        return None
    source = source_root.resolve()
    if not (source / "pyproject.toml").is_file():
        return None
    runtime_python = (
        application_data_root() / "agent-runtime/bin/python"
    )
    if not runtime_python.is_file():
        return None
    result = subprocess.run(
        [
            str(runtime_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--upgrade",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Mac 后台代码更新失败，旧版本仍然保留。")
    return runtime_python


def install_launch_agent(
    *,
    python_path: Path | None = None,
    data_directory: Path | None = None,
    config_source: Path | None = None,
) -> Path:
    data_root = data_directory or application_data_root()
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
    if config_source and config_source.is_file():
        installed_config = data_root / "firebase-public-config.json"
        shutil.copy2(config_source, installed_config)
        installed_config.chmod(0o600)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "mac_agent.main"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "agent.log"),
        "StandardErrorPath": str(logs / "agent-error.log"),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "PATH": (
                f"{data_root}/tools/ffmpeg-7.1-imageio-0.6.0:"
                "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            ),
            "AUDIOBOOK_QWEN_PYTHON": str(
                data_root / "qwen-runtime/bin/python"
            ),
            "AUDIOBOOK_DATA_ROOT": str(data_root),
            "AUDIOBOOK_QWEN_MODEL": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "HF_HOME": str(data_root / "models/huggingface"),
        },
    }
    temporary = target.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return target


def install_watchdog(
    *,
    python_path: Path | None = None,
    data_directory: Path | None = None,
) -> Path:
    data_root = data_directory or application_data_root()
    installed_runtime = data_root / "agent-runtime/bin/python"
    python = (
        python_path
        or (installed_runtime if installed_runtime.is_file() else Path(sys.prefix) / "bin/python")
    ).absolute()
    logs = data_root / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    agents = Path.home() / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    target = agents / f"{WATCHDOG_LABEL}.plist"
    payload = {
        "Label": WATCHDOG_LABEL,
        "ProgramArguments": [str(python), "-m", "mac_agent.watchdog"],
        "StartInterval": 60,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "watchdog.log"),
        "StandardErrorPath": str(logs / "watchdog-error.log"),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "AUDIOBOOK_DATA_ROOT": str(data_root),
        },
    }
    temporary = target.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return target


def ensure_launch_agent(path: Path, label: str) -> None:
    domain = f"gui/{os.getuid()}"
    registered = subprocess.run(
        ["launchctl", "print", f"{domain}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if registered.returncode == 0:
        return
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Mac 后台守护启动失败：{result.stderr.strip() or '没有返回原因'}")


def reload_launch_agent(path: Path) -> None:
    domain = f"gui/{os.getuid()}"
    unloaded = subprocess.run(
        ["launchctl", "bootout", f"{domain}/{LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if unloaded.returncode != 0:
        unloaded = subprocess.run(
            ["launchctl", "bootout", domain, str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    if unloaded.returncode == 0:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            registered = subprocess.run(
                ["launchctl", "print", f"{domain}/{LABEL}"],
                capture_output=True,
                text=True,
                check=False,
            ).returncode == 0
            try:
                with socket.create_connection(("127.0.0.1", AGENT_PORT), timeout=0.1):
                    port_open = True
            except OSError:
                port_open = False
            if not registered and not port_open:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("旧版 Mac 后台未能在 12 秒内安全退出。")

    result: subprocess.CompletedProcess[str] | None = None
    for delay in (0.0, 0.5, 1.5, 3.0):
        if delay:
            time.sleep(delay)
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            break
    if result is None or result.returncode != 0:
        detail = result.stderr.strip() if result is not None else "没有返回结果"
        raise RuntimeError(f"Mac 后台启动项安装失败：{detail}")
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> None:
    path = install_launch_agent()
    watchdog = install_watchdog()
    ensure_launch_agent(watchdog, WATCHDOG_LABEL)
    reload_launch_agent(path)
    print("听书工具已设置为登录后自动启动。")


if __name__ == "__main__":
    main()
