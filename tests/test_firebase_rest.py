from __future__ import annotations

import json
import ssl
from typing import Any

import pytest

from mac_agent.firebase_rest import (
    FirebasePublicConfig,
    FirebaseRestClient,
    FirebaseRestError,
    Identity,
    UrllibTransport,
)


class FakeTokenStore:
    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.writes: list[str] = []
        self.deletes = 0

    def read(self) -> str | None:
        return self.value

    def write(self, token: str) -> None:
        self.value = token
        self.writes.append(token)

    def delete(self) -> None:
        self.value = None
        self.deletes += 1


class FakeTransport:
    def __init__(self, responses: list[tuple[int, dict[str, Any]]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, bytes]:
        self.requests.append((method, url, headers, body))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode("utf-8")


class FakeHttpResponse:
    status = 200

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"{}"


def test_urllib_transport_uses_a_verified_ca_context(monkeypatch: Any) -> None:
    captured_context: ssl.SSLContext | None = None

    def fake_urlopen(
        _request: object,
        *,
        timeout: int,
        context: ssl.SSLContext,
    ) -> FakeHttpResponse:
        nonlocal captured_context
        assert timeout == 20
        captured_context = context
        return FakeHttpResponse()

    monkeypatch.setattr("mac_agent.firebase_rest.urllib.request.urlopen", fake_urlopen)
    transport = UrllibTransport()

    assert transport.request("GET", "https://example.test", headers={}, body=None) == (200, b"{}")
    assert captured_context is transport.ssl_context
    assert transport.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert transport.ssl_context.check_hostname is True


def test_anonymous_worker_token_goes_to_store_and_pairing_uses_only_code_hash() -> None:
    store = FakeTokenStore()
    transport = FakeTransport(
        [
            (
                200,
                {"idToken": "short-id-token", "refreshToken": "keychain-only", "localId": "worker-1"},
            ),
            (200, {"writeResults": []}),
        ]
    )
    client = FirebaseRestClient(
        FirebasePublicConfig(api_key="public-web-key", project_id="demo-project"),
        token_store=store,
        transport=transport,
    )

    assert client.create_pairing_request("123456") == "worker-1"
    assert store.writes == ["keychain-only"]
    commit_body = (transport.requests[1][3] or b"").decode("utf-8")
    assert "123456" not in commit_body
    assert "keychain-only" not in commit_body
    assert "short-id-token" not in commit_body
    assert "created_at" in commit_body
    assert transport.requests[1][2]["Authorization"] == "Bearer short-id-token"


def test_existing_refresh_token_is_reused_without_creating_another_identity() -> None:
    store = FakeTokenStore("existing-refresh")
    transport = FakeTransport(
        [
            (
                200,
                {
                    "id_token": "new-id-token",
                    "refresh_token": "rotated-refresh",
                    "user_id": "worker-1",
                },
            )
        ]
    )
    client = FirebaseRestClient(
        FirebasePublicConfig(api_key="public-web-key", project_id="demo-project"),
        token_store=store,
        transport=transport,
    )

    assert client.authenticate().local_id == "worker-1"
    assert "securetoken.googleapis.com" in transport.requests[0][1]
    assert store.value == "rotated-refresh"


def test_link_status_distinguishes_active_and_revoked_workers() -> None:
    active_transport = FakeTransport([
        (200, {"fields": {"revoked_at": {"nullValue": None}}}),
    ])
    active = FirebaseRestClient(
        FirebasePublicConfig(api_key="public-web-key", project_id="demo-project"),
        token_store=FakeTokenStore(),
        transport=active_transport,
    )
    active._identity = Identity("id-token", "refresh-token", "worker-1")
    assert active.is_linked() is True

    revoked_transport = FakeTransport([
        (200, {"fields": {"revoked_at": {"timestampValue": "2026-07-17T00:00:00Z"}}}),
    ])
    revoked = FirebaseRestClient(
        FirebasePublicConfig(api_key="public-web-key", project_id="demo-project"),
        token_store=FakeTokenStore(),
        transport=revoked_transport,
    )
    revoked._identity = Identity("id-token", "refresh-token", "worker-1")
    assert revoked.is_linked() is False


def test_firestore_permission_error_keeps_private_response_details() -> None:
    transport = FakeTransport([(403, {"error": {"status": "PERMISSION_DENIED"}})])
    client = FirebaseRestClient(
        FirebasePublicConfig(api_key="public-web-key", project_id="demo-project"),
        token_store=FakeTokenStore(),
        transport=transport,
    )

    with pytest.raises(FirebaseRestError) as captured:
        client._json_request("读取私密数据", "GET", "https://example.test")

    assert captured.value.code == "FIRESTORE_PERMISSION_DENIED"
    assert captured.value.details["http_status"] == 403
    assert "PERMISSION_DENIED" in captured.value.details["response"]


def test_firestore_quota_error_enables_local_generation_message() -> None:
    transport = FakeTransport([(429, {"error": {"status": "RESOURCE_EXHAUSTED"}})])
    client = FirebaseRestClient(
        FirebasePublicConfig(api_key="public-web-key", project_id="demo-project"),
        token_store=FakeTokenStore(),
        transport=transport,
    )

    with pytest.raises(FirebaseRestError) as captured:
        client._json_request("检查生成队列", "GET", "https://example.test")

    assert captured.value.code == "FIREBASE_QUOTA_EXHAUSTED"
    assert captured.value.details["http_status"] == 429
    assert "本地生成仍可继续" in str(captured.value)
