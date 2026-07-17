from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Protocol

from .firebase_rest import FirebasePublicConfig, FirebaseRestClient


@dataclass(frozen=True)
class PairingCode:
    code: str
    expires_in: int = 540


class PairingProvider(Protocol):
    @property
    def configured(self) -> bool: ...

    def start(self) -> PairingCode: ...

    def is_linked(self) -> bool: ...


class FirebasePairingProvider:
    def __init__(self, client: FirebaseRestClient | None) -> None:
        self.client = client

    @classmethod
    def from_default_config(cls) -> FirebasePairingProvider:
        config = FirebasePublicConfig.load()
        return cls(FirebaseRestClient(config) if config else None)

    @property
    def configured(self) -> bool:
        return self.client is not None

    def start(self) -> PairingCode:
        if not self.client:
            raise RuntimeError("云同步尚未配置完成，当前仍可使用本机书架。")
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.client.create_pairing_request(code)
        return PairingCode(code=code)

    def is_linked(self) -> bool:
        return bool(self.client and self.client.is_linked())
