from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_scripts_are_valid_and_double_click_apps_are_executable() -> None:
    scripts = [
        ROOT / "installer/install.sh",
        ROOT / "installer/update.sh",
        ROOT / "installer/update-agent-when-idle.sh",
        ROOT / "installer/uninstall.sh",
        ROOT / "installer/repair.sh",
        ROOT / "installer/build-installer.sh",
    ]
    result = subprocess.run(["/bin/bash", "-n", *map(str, scripts)], check=False)
    assert result.returncode == 0
    executables = list((ROOT / "installer/apps").glob("*.app/Contents/MacOS/*"))
    assert len(executables) == 4
    assert all(os.access(path, os.X_OK) for path in executables)


def test_idle_updater_waits_for_checkpoint_and_runs_runtime_repair(tmp_path: Path) -> None:
    source = tmp_path / "project"
    installer = source / "installer"
    installer.mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    fake_install = installer / "install.sh"
    fake_install.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" > "$AUDIOBOOK_DATA_ROOT/update-call.txt"\n',
        encoding="utf-8",
    )
    fake_install.chmod(0o700)
    data_root = tmp_path / "data"
    environment = {
        **os.environ,
        "AUDIOBOOK_DATA_ROOT": str(data_root),
        "AUDIOBOOK_IDLE_UPDATE_POLL_SECONDS": "0",
        "AUDIOBOOK_IDLE_UPDATE_SETTLE_SECONDS": "0",
    }

    result = subprocess.run(
        [
            "/bin/bash",
            str(ROOT / "installer/update-agent-when-idle.sh"),
            "--source-root",
            str(source),
            "--marker",
            "test-update.done",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (data_root / "state/test-update.done").is_file()
    call = (data_root / "update-call.txt").read_text(encoding="utf-8")
    assert f"--repair runtime --source-root {source} --skip-model-test" in call
    script = (ROOT / "installer/update-agent-when-idle.sh").read_text(encoding="utf-8")
    assert "/Users/" not in script


def test_installer_has_pinned_verified_arm64_ffmpeg_fallback() -> None:
    script = (ROOT / "installer/install.sh").read_text(encoding="utf-8")
    assert 'FFMPEG_VERSION="7.1"' in script
    assert 'IMAGEIO_FFMPEG_VERSION="0.6.0"' in script
    assert 'FFMPEG_WHEEL_SHA256="b1ae3173' in script
    assert 'FFMPEG_BINARY_SHA256="6d175a47' in script
    assert "files.pythonhosted.org" in script
    assert (ROOT / "installer/FFMPEG-NOTICE.txt").is_file()


def test_installer_tracks_agent_and_qwen_swaps_independently() -> None:
    script = (ROOT / "installer/install.sh").read_text(encoding="utf-8")
    assert "AGENT_RUNTIME_SWAPPED=0" in script
    assert "QWEN_RUNTIME_SWAPPED=0" in script
    assert 'if [[ "$QWEN_RUNTIME_SWAPPED" -eq 1 ]]' in script
    assert 'if [[ "$AGENT_RUNTIME_SWAPPED" -eq 1 ]]' in script
    assert 'else\n    safe_remove_runtime "$current"' in script
    assert "shasum -a 256" in script


def test_uninstaller_removes_service_but_preserves_books_voices_and_models(tmp_path: Path) -> None:
    data_root = tmp_path / "Library/Application Support/听见书页"
    for name in ("agent-runtime", "qwen-runtime", "tools", "installer", "books", "voices", "models"):
        directory = data_root / name
        directory.mkdir(parents=True)
        (directory / "marker").write_text(name, encoding="utf-8")
    environment = {
        **os.environ,
        "HOME": str(tmp_path),
        "AUDIOBOOK_DATA_ROOT": str(data_root),
    }
    launch_agents = tmp_path / "Library/LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "io.github.mkasumi1007.audiobook-mac-agent.plist").write_text(
        "main",
        encoding="utf-8",
    )
    watchdog = launch_agents / "io.github.mkasumi1007.audiobook-mac-agent-watchdog.plist"
    watchdog.write_text("watchdog", encoding="utf-8")
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "installer/uninstall.sh")],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (data_root / "agent-runtime").exists()
    assert not (data_root / "qwen-runtime").exists()
    assert (data_root / "books/marker").read_text(encoding="utf-8") == "books"
    assert (data_root / "voices/marker").read_text(encoding="utf-8") == "voices"
    assert (data_root / "models/marker").read_text(encoding="utf-8") == "models"
    assert not watchdog.exists()
