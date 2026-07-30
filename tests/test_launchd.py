from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

from mac_agent.launchd import (
    LABEL,
    WATCHDOG_LABEL,
    ensure_launch_agent,
    install_launch_agent,
    install_watchdog,
    refresh_installed_runtime,
    reload_launch_agent,
)


def test_launch_agent_is_private_bounded_and_contains_no_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = install_launch_agent(python_path=tmp_path / "venv/bin/python")
    assert target == tmp_path / "Library/LaunchAgents" / f"{LABEL}.plist"
    with target.open("rb") as handle:
        payload = plistlib.load(handle)
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["ProgramArguments"][-2:] == ["-m", "mac_agent.main"]
    text = target.read_text(encoding="utf-8")
    assert "token" not in text.lower()
    assert "password" not in text.lower()
    assert payload["EnvironmentVariables"]["HF_HOME"].endswith("models/huggingface")
    assert payload["EnvironmentVariables"]["AUDIOBOOK_DATA_ROOT"].endswith("听见书页")
    assert "tools/ffmpeg-7.1-imageio-0.6.0" in payload["EnvironmentVariables"]["PATH"]


def test_watchdog_is_periodic_and_uses_the_private_runtime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    python = tmp_path / "private-runtime/bin/python"
    target = install_watchdog(python_path=python, data_directory=tmp_path / "private-data")
    assert target == tmp_path / "Library/LaunchAgents" / f"{WATCHDOG_LABEL}.plist"
    with target.open("rb") as handle:
        payload = plistlib.load(handle)
    assert payload["ProgramArguments"] == [str(python), "-m", "mac_agent.watchdog"]
    assert "RunAtLoad" not in payload
    assert payload["StartInterval"] == 60
    assert "KeepAlive" not in payload
    assert "token" not in target.read_text(encoding="utf-8").lower()


def test_ensure_launch_agent_bootstraps_only_when_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command[1] == "print" else 0, "", "")

    monkeypatch.setattr("mac_agent.launchd.subprocess.run", run)
    path = tmp_path / "watchdog.plist"
    ensure_launch_agent(path, WATCHDOG_LABEL)
    assert [command[1] for command in calls] == ["print", "bootstrap"]


def test_installer_refreshes_existing_private_runtime_from_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "project"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    runtime = tmp_path / "Library/Application Support/听见书页/agent-runtime/bin/python"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "updated", "")

    monkeypatch.setattr("mac_agent.launchd.subprocess.run", run)
    assert refresh_installed_runtime(source) == runtime
    assert calls[0][0] == str(runtime)
    assert calls[0][-1] == str(source)


def test_reload_waits_for_shutdown_and_retries_bootstrap(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    bootstrap_attempts = 0

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal bootstrap_attempts
        calls.append(command)
        if command[1] == "print":
            return subprocess.CompletedProcess(command, 1, "", "not found")
        if command[1] == "bootstrap":
            bootstrap_attempts += 1
            return subprocess.CompletedProcess(
                command,
                0 if bootstrap_attempts == 2 else 5,
                "",
                "Input/output error" if bootstrap_attempts == 1 else "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    def no_connection(*_args: object, **_kwargs: object) -> None:
        raise OSError("closed")

    monkeypatch.setattr("mac_agent.launchd.subprocess.run", run)
    monkeypatch.setattr("mac_agent.launchd.socket.create_connection", no_connection)
    monkeypatch.setattr("mac_agent.launchd.time.sleep", lambda _seconds: None)
    reload_launch_agent(tmp_path / "agent.plist")
    assert bootstrap_attempts == 2
    assert [command[1] for command in calls] == [
        "bootout",
        "print",
        "bootstrap",
        "bootstrap",
        "kickstart",
    ]
