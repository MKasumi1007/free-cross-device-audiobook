from __future__ import annotations

import subprocess

from mac_agent.resources import ResourcePolicy


def test_missing_macos_resource_commands_are_treated_as_unknown(monkeypatch) -> None:
    def missing(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("command unavailable")

    monkeypatch.setattr(subprocess, "run", missing)

    assert ResourcePolicy._on_ac_power() is True
    assert ResourcePolicy.available_memory_bytes() is None
