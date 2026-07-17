from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from time import monotonic


DEFAULT_ALLOWED_ORIGINS = frozenset(
    {
        "https://mkasumi1007.github.io",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    }
)


@dataclass(frozen=True)
class TokenRecord:
    origin: str
    expires_at: float


class CsrfTokenStore:
    def __init__(self, ttl_seconds: int = 120) -> None:
        self.ttl_seconds = ttl_seconds
        self._tokens: dict[str, TokenRecord] = {}
        self._lock = threading.Lock()

    def issue(self, origin: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            self._tokens[token] = TokenRecord(origin=origin, expires_at=monotonic() + self.ttl_seconds)
        return token

    def consume(self, token: str, origin: str) -> bool:
        with self._lock:
            self._purge()
            record = self._tokens.pop(token, None)
        return bool(record and secrets.compare_digest(record.origin, origin))

    def _purge(self) -> None:
        now = monotonic()
        self._tokens = {token: record for token, record in self._tokens.items() if record.expires_at > now}
