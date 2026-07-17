from __future__ import annotations

import subprocess
from typing import Protocol


class TokenStore(Protocol):
    def read(self) -> str | None: ...

    def write(self, token: str) -> None: ...

    def delete(self) -> None: ...


class MacOSKeychainTokenStore:
    """Stores the worker refresh token in Keychain, never in a project file."""

    def __init__(self, project_id: str) -> None:
        self.service = "io.github.mkasumi1007.audiobook.firebase-refresh-token"
        self.account = project_id

    def read(self) -> str | None:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def write(self, token: str) -> None:
        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
                token,
                "-U",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def delete(self) -> None:
        subprocess.run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
