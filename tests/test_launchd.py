from __future__ import annotations

import plistlib
from pathlib import Path

from mac_agent.launchd import LABEL, install_launch_agent


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
