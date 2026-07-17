from __future__ import annotations

from mac_agent.reconciliation import AudioReconciler
from mac_agent.task_cloud import RemoteAudioRecord


class FakeAudio:
    def list_assets(self, _book_id: str) -> list[dict[str, object]]:
        return [
            {"apiUrl": "https://api.github.test/releases/assets/10", "size": 999},
            {"apiUrl": "https://api.github.test/releases/assets/11", "size": 100},
        ]


class FakeData:
    def asset_exists(self, _asset_id: int, _url: str) -> bool:
        return False

    def list_paths(self, _book_id: str) -> list[str]:
        return ["books/book-a/timeline-orphan.json.gz"]

    def _path_from_url(self, _url: str) -> str:
        return "books/book-a/timeline-ready.json.gz"


def test_reconciliation_reports_but_never_deletes_orphans() -> None:
    reconciler = AudioReconciler.__new__(AudioReconciler)
    reconciler.audio = FakeAudio()  # type: ignore[assignment]
    reconciler.data = FakeData()  # type: ignore[assignment]
    records = [RemoteAudioRecord(
        owner_uid="owner-a",
        book_id="book-a",
        chunk_id="chunk-a",
        status="READY",
        asset_id=10,
        asset_url="https://example/audio",
        byte_size=100,
        timeline_asset_id=20,
        timeline_url="https://example/timeline",
    )]
    report = reconciler.compare(records)
    assert report.checked_books == 1
    assert report.damaged == ("book-a/chunk-a:audio-size",)
    assert report.missing == ("book-a/chunk-a:timeline",)
    assert "book-a/release:11" in report.orphan
    assert any("timeline-orphan" in item for item in report.orphan)
    assert "不会自动删除" in report.summary()
