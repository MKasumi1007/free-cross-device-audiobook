from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .release_assets import GitHubReleasePublisher
from .repository_assets import GitHubRepositoryAssetPublisher
from .task_cloud import FirestoreWorkerTasks, RemoteAudioRecord


@dataclass(frozen=True)
class ReconciliationReport:
    missing: tuple[str, ...]
    damaged: tuple[str, ...]
    orphan: tuple[str, ...]
    checked_books: int

    def summary(self) -> str:
        return json.dumps({
            "missing": list(self.missing[:100]),
            "damaged": list(self.damaged[:100]),
            "orphan": list(self.orphan[:100]),
            "note": "仅报告，不会自动删除任何远端孤儿资产。",
        }, ensure_ascii=False, separators=(",", ":"))


def _release_asset_id(asset: dict[str, Any]) -> int | None:
    try:
        return int(str(asset["apiUrl"]).rstrip("/").rsplit("/", 1)[-1])
    except (KeyError, TypeError, ValueError):
        return None


class AudioReconciler:
    def __init__(
        self,
        tasks: FirestoreWorkerTasks,
        repository: str,
    ) -> None:
        self.tasks = tasks
        self.audio = GitHubReleasePublisher(repository)
        self.data = GitHubRepositoryAssetPublisher(repository)

    def run(self, owner_uid: str) -> ReconciliationReport:
        records = self.tasks.audio_inventory(owner_uid)
        report = self.compare(records)
        self.tasks.record_reconciliation(
            owner_uid,
            missing_assets=len(report.missing),
            damaged_assets=len(report.damaged),
            orphan_assets=len(report.orphan),
            checked_books=report.checked_books,
            summary=report.summary(),
        )
        return report

    def compare(self, records: list[RemoteAudioRecord]) -> ReconciliationReport:
        missing: list[str] = []
        damaged: list[str] = []
        orphan: list[str] = []
        book_ids = sorted({record.book_id for record in records if record.book_id})
        for book_id in book_ids:
            book_records = [record for record in records if record.book_id == book_id]
            referenced_audio = {
                record.asset_id
                for record in book_records
                if record.status != "DELETED" and record.asset_id is not None
            }
            assets = self.audio.list_assets(book_id)
            by_id = {
                asset_id: asset
                for asset in assets
                if (asset_id := _release_asset_id(asset)) is not None
            }
            for record in book_records:
                if record.status != "READY":
                    continue
                if record.asset_id is None or record.asset_id not in by_id:
                    missing.append(f"{book_id}/{record.chunk_id}:audio")
                else:
                    remote_size = int(by_id[record.asset_id].get("size") or 0)
                    if remote_size and record.byte_size and remote_size != record.byte_size:
                        damaged.append(f"{book_id}/{record.chunk_id}:audio-size")
                if (
                    record.timeline_asset_id is None
                    or not record.timeline_url
                    or not self.data.asset_exists(record.timeline_asset_id, record.timeline_url)
                ):
                    missing.append(f"{book_id}/{record.chunk_id}:timeline")
            for asset_id in sorted(set(by_id) - referenced_audio):
                orphan.append(f"{book_id}/release:{asset_id}")

            referenced_paths = {
                self.data._path_from_url(record.timeline_url)
                for record in book_records
                if record.status != "DELETED" and record.timeline_url
            }
            for path in self.data.list_paths(book_id):
                if "/timeline-" in path and path not in referenced_paths:
                    orphan.append(f"{book_id}/branch:{path}")
        return ReconciliationReport(
            missing=tuple(sorted(set(missing))),
            damaged=tuple(sorted(set(damaged))),
            orphan=tuple(sorted(set(orphan))),
            checked_books=len(book_ids),
        )
