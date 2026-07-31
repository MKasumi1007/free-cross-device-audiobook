from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GenerationSettings:
    paused: bool = False
    only_on_ac_power: bool = True
    minimum_available_memory_bytes: int = 2 * 1024 * 1024 * 1024
    poll_seconds: int = 15
    idle_poll_seconds: int = 5 * 60


class ResourcePolicy:
    def __init__(self, settings_path: Path) -> None:
        self.settings_path = settings_path

    def load(self) -> GenerationSettings:
        if not self.settings_path.exists():
            settings = GenerationSettings()
            self.save(settings)
            return settings
        try:
            return GenerationSettings(**json.loads(self.settings_path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return GenerationSettings()

    def save(self, settings: GenerationSettings) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.settings_path.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.settings_path.chmod(0o600)

    def pause_reason(self) -> str | None:
        settings = self.load()
        if settings.paused:
            return "USER_PAUSED"
        if settings.only_on_ac_power and not self._on_ac_power():
            return "WAITING_FOR_AC_POWER"
        available = self.available_memory_bytes()
        if available is not None and available < settings.minimum_available_memory_bytes:
            return "MEMORY_PRESSURE"
        return None

    @staticmethod
    def _on_ac_power() -> bool:
        try:
            result = subprocess.run(
                ["pmset", "-g", "batt"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return True
        return "AC Power" in result.stdout

    @staticmethod
    def available_memory_bytes() -> int | None:
        try:
            result = subprocess.run(
                ["vm_stat"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None
        page_size = 4096
        available_pages = 0
        for line in result.stdout.splitlines():
            if "page size of" in line:
                try:
                    page_size = int(line.split("page size of", 1)[1].split("bytes", 1)[0].strip())
                except ValueError:
                    pass
            if line.startswith(("Pages free:", "Pages inactive:", "Pages speculative:")):
                try:
                    available_pages += int(line.rsplit(" ", 1)[-1].rstrip("."))
                except ValueError:
                    pass
        return available_pages * page_size
