from __future__ import annotations

import subprocess
from pathlib import Path

from mac_agent.watchdog import maintain_agent


def test_watchdog_does_nothing_while_agent_port_is_open(monkeypatch) -> None:
    monkeypatch.setattr("mac_agent.watchdog.agent_port_open", lambda: True)

    def unexpected(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("launchctl must not run for a healthy Agent")

    monkeypatch.setattr("mac_agent.watchdog.subprocess.run", unexpected)
    assert maintain_agent() == "healthy"


def test_watchdog_bootstraps_an_unregistered_agent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("mac_agent.watchdog.agent_port_open", lambda: False)
    monkeypatch.setenv("HOME", str(tmp_path))
    plist = (
        tmp_path
        / "Library/LaunchAgents/io.github.mkasumi1007.audiobook-mac-agent.plist"
    )
    plist.parent.mkdir(parents=True)
    plist.write_text("test", encoding="utf-8")
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command[1] == "print" else 0, "", "")

    monkeypatch.setattr("mac_agent.watchdog.subprocess.run", run)
    assert maintain_agent() == "registered"
    assert [command[1] for command in calls] == ["print", "bootstrap"]
    assert calls[-1][-1] == str(plist)


def test_watchdog_restarts_a_registered_but_stopped_agent(monkeypatch) -> None:
    monkeypatch.setattr("mac_agent.watchdog.agent_port_open", lambda: False)
    monkeypatch.setattr("mac_agent.watchdog.wait_for_agent", lambda **_kwargs: False)
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("mac_agent.watchdog.subprocess.run", run)
    assert maintain_agent() == "restarted"
    assert [command[1] for command in calls] == ["print", "print", "kickstart"]


def test_watchdog_bootstraps_when_agent_finishes_bootout_during_wait(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("mac_agent.watchdog.agent_port_open", lambda: False)
    monkeypatch.setattr("mac_agent.watchdog.wait_for_agent", lambda **_kwargs: False)
    monkeypatch.setenv("HOME", str(tmp_path))
    plist = (
        tmp_path
        / "Library/LaunchAgents/io.github.mkasumi1007.audiobook-mac-agent.plist"
    )
    plist.parent.mkdir(parents=True)
    plist.write_text("test", encoding="utf-8")
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0 if len(calls) != 2 else 1, "", "")

    monkeypatch.setattr("mac_agent.watchdog.subprocess.run", run)
    assert maintain_agent() == "registered"
    assert [command[1] for command in calls] == ["print", "print", "bootstrap"]
