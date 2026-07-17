from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

from mac_agent.launchd import LABEL, install_launch_agent, refresh_installed_runtime


def test_launch_agent_is_private_bounded_and_contains_no_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = install_launch_agent(python_path=tmp_path / "venv/bin/python")
    assert target == tmp_path / "Library/LaunchAgents" / f"{LABEL}.plist"
    with target.open("rb") as handle:
        payload = plistlib.load(handle)
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert payload["ProgramArguments"][-2:] == ["-m", "mac_agent.main"]
    text = target.read_text(encoding="utf-8")
    assert "token" not in text.lower()
    assert "password" not in text.lower()


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
