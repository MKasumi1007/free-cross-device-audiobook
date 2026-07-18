from __future__ import annotations

from pathlib import Path

from mac_agent.error_reporting import LocalErrorReporter


def test_diagnostics_write_failure_never_masks_original_error(tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied", encoding="utf-8")
    reporter = LocalErrorReporter(blocked / "diagnostics.jsonl")

    payload = reporter.record("test.operation", RuntimeError("original failure"))

    assert payload["message"] == "original failure"
    assert payload["error_code"] == "UNEXPECTED_ERROR"
