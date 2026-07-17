from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mac_agent.app import create_app
from mac_agent.library import LocalLibrary


class FakePicker:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.calls = 0

    def choose(self) -> Path | None:
        self.calls += 1
        return self.path


ORIGIN = "http://127.0.0.1:5173"


def make_client(tmp_path: Path, picker: FakePicker) -> TestClient:
    library = LocalLibrary(tmp_path / "library", picker)
    return TestClient(create_app(library=library))


def issue_token(client: TestClient) -> str:
    response = client.get("/v1/session", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def test_rejects_missing_and_cross_site_origins(tmp_path: Path) -> None:
    client = make_client(tmp_path, FakePicker(None))
    assert client.get("/v1/health").status_code == 403
    assert client.get("/v1/health", headers={"Origin": "https://evil.example"}).status_code == 403


def test_preflight_is_explicit_and_private_network_compatible(tmp_path: Path) -> None:
    client = make_client(tmp_path, FakePicker(None))
    response = client.options(
        "/v1/books/choose",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-private-network"] == "true"


def test_import_uses_native_picker_and_one_time_csrf(tmp_path: Path) -> None:
    book_path = tmp_path / "chosen.txt"
    book_path.write_text("第一章 试读\n这段文字来自测试。", encoding="utf-8")
    picker = FakePicker(book_path)
    client = make_client(tmp_path, picker)
    token = issue_token(client)
    headers = {"Origin": ORIGIN, "X-Audiobook-CSRF": token}

    response = client.post(
        "/v1/books/choose",
        headers=headers,
        json={"import_as_copy": False, "rights_confirmed": False},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "chosen"
    assert response.json()["publication_mode"] == "LOCAL_ONLY"
    assert picker.calls == 1

    reused = client.post(
        "/v1/books/choose",
        headers=headers,
        json={"import_as_copy": False, "rights_confirmed": False},
    )
    assert reused.status_code == 403
    assert picker.calls == 1


def test_request_cannot_supply_an_arbitrary_file_path(tmp_path: Path) -> None:
    picker = FakePicker(None)
    client = make_client(tmp_path, picker)
    response = client.post(
        "/v1/books/choose",
        headers={"Origin": ORIGIN, "X-Audiobook-CSRF": issue_token(client)},
        json={"path": "/etc/passwd"},
    )
    assert response.status_code == 422
    assert picker.calls == 0


def test_bad_encoding_returns_chinese_reason(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.txt"
    bad_path.write_bytes(b"\x81")
    client = make_client(tmp_path, FakePicker(bad_path))
    response = client.post(
        "/v1/books/choose",
        headers={"Origin": ORIGIN, "X-Audiobook-CSRF": issue_token(client)},
        json={},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "BAD_ENCODING"
    assert "编码" in response.json()["error"]
