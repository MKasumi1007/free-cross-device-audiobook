from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

from .launchd import AGENT_PORT, LABEL


def agent_port_open(*, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", AGENT_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_agent(*, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if agent_port_open(timeout=min(0.5, timeout)):
            return True
        time.sleep(0.5)
    return agent_port_open(timeout=min(0.5, timeout))


def maintain_agent() -> str:
    if agent_port_open():
        return "healthy"

    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    registered = _launchctl("print", service)
    if registered.returncode == 0:
        if wait_for_agent():
            return "healthy"

        # bootout can leave a terminating service visible for a few seconds.
        if _launchctl("print", service).returncode == 0:
            result = _launchctl("kickstart", "-k", service)
            if result.returncode != 0:
                if wait_for_agent(timeout=5):
                    return "healthy"
                raise RuntimeError(f"Mac Agent 重新启动失败：{result.stderr.strip()}")
            return "restarted"

    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    if not plist.is_file():
        raise FileNotFoundError("Mac Agent 登录启动文件不存在，请重新运行安装器。")
    result = _launchctl("bootstrap", domain, str(plist))
    if result.returncode != 0:
        raise RuntimeError(f"Mac Agent 重新注册失败：{result.stderr.strip()}")
    return "registered"


def _launchctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> None:
    action = maintain_agent()
    if action != "healthy":
        print(f"Mac Agent watchdog: {action}")


if __name__ == "__main__":
    main()
