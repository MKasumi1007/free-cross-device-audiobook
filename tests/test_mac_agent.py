from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path

import httpx
from audiobook_core.parser import parse_book
from fastapi.testclient import TestClient
from mac_agent.app import create_app
from mac_agent.library import LocalLibrary
from mac_agent.pairing import PairingCode
from mac_agent.voice import VoiceRegistry

from tests.fixtures.builders import make_epub_with_placeholder_nav


class FakePicker:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.calls = 0

    def choose(self) -> Path | None:
        self.calls += 1
        return self.path


class FakePairing:
    configured = True

    def __init__(self) -> None:
        self.starts = 0

    def start(self) -> PairingCode:
        self.starts += 1
        return PairingCode(code="314159")

    def is_linked(self) -> bool:
        return True


class FakeVoiceRunner:
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if "-progress" in command:
            return subprocess.CompletedProcess(command, 0, "out_time_us=15000000\nprogress=end\n", "")
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"normalized private voice")
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected command: {command}")


class FakePreviews:
    def __init__(self) -> None:
        self.starts = 0

    def status(self) -> dict[str, str | bool]:
        return {"state": "IDLE", "error": "", "model_loaded": False}

    def start(self) -> dict[str, str | bool]:
        self.starts += 1
        return {"state": "GENERATING", "error": "", "model_loaded": True}

    def unload(self) -> None:
        return None


class FakeDiagnostics:
    def __init__(self) -> None:
        self.repairs: list[str] = []

    def report(self, **_kwargs: object) -> dict[str, object]:
        return {
            "schema_version": 1,
            "checked_at": "2026-07-18T00:00:00+00:00",
            "agent_version": "0.2.0",
            "agent_port": 17832,
            "data_root": "/private/application-support",
            "log_path": "/private/application-support/logs/diagnostics.jsonl",
            "worker": {"state": "IDLE", "error": "", "model_loaded": False},
            "recent_error": None,
            "items": [],
        }

    def start_repair(self, action: str) -> dict[str, str]:
        self.repairs.append(action)
        return {"status": "started", "action": action, "message": "started"}


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
    rejected = client.get("/v1/health", headers={"Origin": "https://evil.example"})
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "ORIGIN_MISMATCH"


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


def test_existing_library_repairs_placeholder_chapter_title(tmp_path: Path) -> None:
    parsed = parse_book(make_epub_with_placeholder_nav(tmp_path / "placeholder.epub"))
    payload = parsed.to_dict()
    payload["chapters"][0]["title"] = "Section0001"
    stored = tmp_path / "library" / parsed.book_id / "book.json"
    stored.parent.mkdir(parents=True)
    stored.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    loaded = LocalLibrary(tmp_path / "library", FakePicker(None)).get(parsed.book_id)

    assert loaded is not None
    assert loaded.chapters[0].title == "献给倾听我故事的伦纳德"


def test_library_removes_only_the_selected_book_record(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    selected = library_root / "book-a"
    selected.mkdir(parents=True)
    (selected / "book.json").write_text("{}", encoding="utf-8")
    unexpected = selected / "keep-me.txt"
    unexpected.write_text("保留", encoding="utf-8")
    other = library_root / "book-b"
    other.mkdir(parents=True)
    (other / "book.json").write_text("{}", encoding="utf-8")
    library = LocalLibrary(library_root, FakePicker(None))

    assert library.remove("book-a") is True
    assert not (selected / "book.json").exists()
    assert unexpected.read_text(encoding="utf-8") == "保留"
    assert (other / "book.json").exists()
    assert library.remove("../book-b") is False


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


def test_pairing_uses_one_time_csrf_and_never_returns_a_token(tmp_path: Path) -> None:
    pairing = FakePairing()
    library = LocalLibrary(tmp_path / "library", FakePicker(None))
    client = TestClient(create_app(library=library, pairing=pairing))
    headers = {"Origin": ORIGIN, "X-Audiobook-CSRF": issue_token(client)}

    response = client.post("/v1/pairing/start", headers=headers, json={})
    assert response.status_code == 200
    assert response.json() == {"code": "314159", "expires_in": 540}
    assert "token" not in response.text.lower()
    assert pairing.starts == 1

    assert client.post("/v1/pairing/start", headers=headers, json={}).status_code == 403
    status = client.get("/v1/pairing/status", headers={"Origin": ORIGIN})
    assert status.json() == {"configured": True, "linked": True}


def test_voice_setup_uses_native_picker_and_never_exposes_private_fields(tmp_path: Path) -> None:
    source = tmp_path / "voice.m4a"
    source.write_bytes(b"voice")
    registry = VoiceRegistry(
        tmp_path / "private-voices",
        FakePicker(source),
        runner=FakeVoiceRunner(),
    )
    previews = FakePreviews()
    library = LocalLibrary(tmp_path / "library", FakePicker(None))
    client = TestClient(create_app(library=library, voices=registry, previews=previews))  # type: ignore[arg-type]

    response = client.post(
        "/v1/voice/choose",
        headers={"Origin": ORIGIN, "X-Audiobook-CSRF": issue_token(client)},
        json={"transcript": "录音对应的准确文字"},
    )
    assert response.status_code == 200
    assert response.json()["confirmed"] is False
    assert "transcript" not in response.text
    assert "audio_path" not in response.text

    preview = client.post(
        "/v1/voice/preview",
        headers={"Origin": ORIGIN, "X-Audiobook-CSRF": issue_token(client)},
        json={},
    )
    assert preview.status_code == 202
    assert previews.starts == 1


def test_diagnostics_reports_runtime_and_repair_requires_one_time_csrf(tmp_path: Path) -> None:
    diagnostics = FakeDiagnostics()
    library = LocalLibrary(tmp_path / "library", FakePicker(None))
    client = TestClient(
        create_app(library=library, diagnostics=diagnostics, pairing=FakePairing())
    )  # type: ignore[arg-type]

    report = client.get("/v1/diagnostics", headers={"Origin": ORIGIN})
    assert report.status_code == 200
    assert report.json()["agent_version"] == "0.2.0"
    assert "token" not in report.text.lower()

    headers = {"Origin": ORIGIN, "X-Audiobook-CSRF": issue_token(client)}
    repaired = client.post(
        "/v1/diagnostics/repair",
        headers=headers,
        json={"action": "model"},
    )
    assert repaired.status_code == 202
    assert diagnostics.repairs == ["model"]
    assert client.post(
        "/v1/diagnostics/repair",
        headers=headers,
        json={"action": "model"},
    ).status_code == 403


def test_slow_diagnostics_does_not_block_health(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class SlowDiagnostics(FakeDiagnostics):
        def report(self, **_kwargs: object) -> dict[str, object]:
            started.set()
            release.wait(timeout=2)
            return {"agent_version": "0.2.0"}

    app = create_app(
        library=LocalLibrary(tmp_path / "library", FakePicker(None)),
        diagnostics=SlowDiagnostics(),
        pairing=FakePairing(),
    )

    async def exercise() -> float:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
            headers={"Origin": ORIGIN},
        ) as client:
            diagnostics = asyncio.create_task(client.get("/v1/diagnostics"))
            assert await asyncio.to_thread(started.wait, 1)
            before = time.monotonic()
            health = await client.get("/v1/health")
            elapsed = time.monotonic() - before
            release.set()
            assert (await diagnostics).status_code == 200
            assert health.status_code == 200
            return elapsed

    assert asyncio.run(exercise()) < 0.2


def test_local_generation_endpoints_manage_queue_and_serve_audio(tmp_path: Path) -> None:
    audio = tmp_path / "ready.m4a"
    audio.write_bytes(b"local audio")

    class FakeWorker:
        def __init__(self) -> None:
            self.enqueued: list[object] = []
            self.actions: list[tuple[list[str], str]] = []
            self.orders: list[list[str]] = []

        def local_status(self) -> dict[str, object]:
            return {
                "schema_version": 1,
                "tasks": [],
                "audio_chunks": [],
                "pending_sync": 0,
                "worker": {"state": "IDLE", "error": "", "model_loaded": False},
            }

        def enqueue_local(
            self,
            owner_uid: str,
            selections: list[dict[str, object]],
            voice_version: str,
        ) -> dict[str, int]:
            self.enqueued.append((owner_uid, selections, voice_version))
            return {"chapters": 1, "created": 1, "resumed": 0, "unchanged": 0}

        def local_action(self, task_ids: list[str], action: str) -> int:
            self.actions.append((task_ids, action))
            return len(task_ids)

        def local_reorder(self, task_ids: list[str]) -> int:
            self.orders.append(task_ids)
            return len(task_ids)

        def local_asset(self, task_id: str, kind: str) -> Path | None:
            return audio if task_id == "task-1" and kind == "audio" else None

    worker = FakeWorker()
    client = TestClient(create_app(
        library=LocalLibrary(tmp_path / "library", FakePicker(None)),
        pairing=FakePairing(),
        worker=worker,
    ))
    origin = {"Origin": ORIGIN}

    assert client.get("/v1/local-generation/status", headers=origin).status_code == 200
    enqueue = client.post(
        "/v1/local-generation/enqueue",
        headers={**origin, "X-Audiobook-CSRF": issue_token(client)},
        json={
            "owner_uid": "owner-1",
            "voice_version": "voice-1",
            "selections": [{"book_id": "book-1", "chapter_ids": ["chapter-1"]}],
        },
    )
    assert enqueue.status_code == 202
    assert enqueue.json()["created"] == 1
    action = client.post(
        "/v1/local-generation/action",
        headers={**origin, "X-Audiobook-CSRF": issue_token(client)},
        json={"task_ids": ["task-1"], "action": "PAUSE"},
    )
    assert action.json() == {"changed": 1}
    reorder = client.post(
        "/v1/local-generation/reorder",
        headers={**origin, "X-Audiobook-CSRF": issue_token(client)},
        json={"task_ids": ["task-1"]},
    )
    assert reorder.json() == {"changed": 1}
    served = client.get("/v1/local-generation/assets/task-1/audio", headers=origin)
    assert served.status_code == 200
    assert served.content == b"local audio"
