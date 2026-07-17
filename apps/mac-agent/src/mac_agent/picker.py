from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol


class BookPicker(Protocol):
    def choose(self) -> Path | None:
        ...


class NativeBookPicker:
    SCRIPT = """
set chosenFile to choose file with prompt "选择要加入书架的 EPUB 或 TXT" of type {"org.idpf.epub-container", "public.plain-text"}
return POSIX path of chosenFile
"""

    def choose(self) -> Path | None:
        result = subprocess.run(
            ["osascript", "-e", self.SCRIPT],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            if "User canceled" in result.stderr or "-128" in result.stderr:
                return None
            raise RuntimeError(result.stderr.strip() or "无法打开 Mac 文件选择器。")
        selected = result.stdout.strip()
        return Path(selected) if selected else None
