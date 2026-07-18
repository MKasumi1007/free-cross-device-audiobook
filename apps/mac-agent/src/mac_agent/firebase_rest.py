from __future__ import annotations

import json
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

import certifi

from .error_reporting import AgentOperationError
from .keychain import MacOSKeychainTokenStore, TokenStore


class FirebaseRestError(AgentOperationError):
    def __init__(
        self,
        user_message: str,
        *,
        code: str = "FIREBASE_API_FAILED",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, user_message, details=details)


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, bytes]: ...


class UrllibTransport:
    def __init__(self) -> None:
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(url, method=method, headers=headers, data=body)
        try:
            with urllib.request.urlopen(
                request,
                timeout=20,
                context=self.ssl_context,
            ) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except urllib.error.URLError as error:
            raise FirebaseRestError(
                "暂时无法连接 Firebase，请检查网络后重试。",
                code="FIREBASE_NETWORK_ERROR",
                details={"reason": repr(error.reason)},
            ) from error


@dataclass(frozen=True)
class FirebasePublicConfig:
    api_key: str
    project_id: str

    @classmethod
    def load(cls, path: Path | None = None) -> FirebasePublicConfig | None:
        candidates = [path] if path else [
            Path.home() / "Library/Application Support/听见书页/firebase-public-config.json",
            Path(__file__).resolve().parents[4] / "config/firebase-public-config.json",
        ]
        for config_path in candidates:
            if config_path is None or not config_path.exists():
                continue
            try:
                value = json.loads(config_path.read_text(encoding="utf-8"))
                api_key = str(value["apiKey"])
                project_id = str(value["projectId"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            return cls(api_key=api_key, project_id=project_id)
        return None


@dataclass(frozen=True)
class Identity:
    id_token: str
    refresh_token: str
    local_id: str
    expires_at: datetime | None = None


class FirebaseRestClient:
    def __init__(
        self,
        config: FirebasePublicConfig,
        *,
        token_store: TokenStore | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        self.config = config
        self.token_store = token_store or MacOSKeychainTokenStore(config.project_id)
        self.transport = transport or UrllibTransport()
        self._identity: Identity | None = None
        self._auth_lock = threading.Lock()

    def _json_request(
        self,
        label: str,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        id_token: str | None = None,
        allowed_statuses: frozenset[int] = frozenset({200}),
    ) -> tuple[int, Any]:
        headers: dict[str, str] = {}
        body: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urllib.parse.urlencode(form).encode("ascii")
        if id_token:
            headers["Authorization"] = f"Bearer {id_token}"
        status, raw = self.transport.request(method, url, headers=headers, body=body)
        if status not in allowed_statuses:
            error_code = (
                "FIREBASE_LOGIN_FAILED"
                if status == 401
                else "FIRESTORE_PERMISSION_DENIED"
                if status == 403
                else "FIREBASE_API_FAILED"
            )
            raise FirebaseRestError(
                f"{label}失败（HTTP {status}），没有保存任何账号凭据。",
                code=error_code,
                details={
                    "firebase_operation": label,
                    "http_status": status,
                    "response": raw.decode("utf-8", errors="replace")[-4_000:],
                },
            )
        if not raw:
            return status, {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise FirebaseRestError(f"{label}返回了无法识别的数据。") from error
        return status, value

    def authenticate(self) -> Identity:
        with self._auth_lock:
            if self._identity and (
                self._identity.expires_at is None
                or self._identity.expires_at > datetime.now(UTC) + timedelta(minutes=1)
            ):
                return self._identity
            if self._identity:
                self._identity = self._refresh(self._identity.refresh_token)
                return self._identity
            refresh_token = self.token_store.read()
            if refresh_token:
                try:
                    self._identity = self._refresh(refresh_token)
                    return self._identity
                except FirebaseRestError:
                    self.token_store.delete()
            self._identity = self._create_anonymous_identity()
            self.token_store.write(self._identity.refresh_token)
            return self._identity

    def _create_anonymous_identity(self) -> Identity:
        _, value = self._json_request(
            "创建 Mac Agent 匿名身份",
            "POST",
            f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={self.config.api_key}",
            payload={"returnSecureToken": True},
        )
        return Identity(
            id_token=str(value["idToken"]),
            refresh_token=str(value["refreshToken"]),
            local_id=str(value["localId"]),
            expires_at=datetime.now(UTC) + timedelta(seconds=int(value.get("expiresIn", 3600))),
        )

    def _refresh(self, refresh_token: str) -> Identity:
        _, value = self._json_request(
            "刷新 Mac Agent 身份",
            "POST",
            f"https://securetoken.googleapis.com/v1/token?key={self.config.api_key}",
            form={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        replacement = str(value.get("refresh_token") or refresh_token)
        if replacement != refresh_token:
            self.token_store.write(replacement)
        return Identity(
            id_token=str(value["id_token"]),
            refresh_token=replacement,
            local_id=str(value["user_id"]),
            expires_at=datetime.now(UTC) + timedelta(seconds=int(value.get("expires_in", 3600))),
        )

    def create_pairing_request(self, code: str) -> str:
        identity = self.authenticate()
        code_hash = sha256(code.encode("ascii")).hexdigest()
        expires_at = datetime.now(UTC) + timedelta(minutes=9)
        document_name = (
            f"projects/{self.config.project_id}/databases/(default)/documents/"
            f"pairingRequests/{code_hash}"
        )
        payload = {
            "writes": [
                {
                    "update": {
                        "name": document_name,
                        "fields": {
                            "code_hash": {"stringValue": code_hash},
                            "worker_uid": {"stringValue": identity.local_id},
                            "owner_uid": {"nullValue": None},
                            "used_at": {"nullValue": None},
                            "attempt_count": {"integerValue": "0"},
                            "expires_at": {"timestampValue": expires_at.isoformat()},
                        },
                    },
                    "currentDocument": {"exists": False},
                    "updateTransforms": [
                        {"fieldPath": "created_at", "setToServerValue": "REQUEST_TIME"}
                    ],
                }
            ]
        }
        self._json_request(
            "创建十分钟配对码",
            "POST",
            f"https://firestore.googleapis.com/v1/projects/{self.config.project_id}/databases/(default)/documents:commit",
            payload=payload,
            id_token=identity.id_token,
        )
        return identity.local_id

    def is_linked(self) -> bool:
        identity = self.authenticate()
        _, value = self._json_request(
            "检查 Mac 连接状态",
            "GET",
            f"https://firestore.googleapis.com/v1/projects/{self.config.project_id}/databases/(default)/documents/workerLinks/{identity.local_id}",
            id_token=identity.id_token,
            allowed_statuses=frozenset({200, 404}),
        )
        fields = value.get("fields", {})
        revoked_at = fields.get("revoked_at", {})
        return bool(fields and isinstance(revoked_at, dict) and "nullValue" in revoked_at)
