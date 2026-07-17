#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from audiobook_core.models import Chapter, ParsedBook, PublicationMode, SegmentKind, TextSegment
from mac_agent.book_assets import BookTextPublisher
from mac_agent.repository_assets import GitHubRepositoryAssetPublisher


REPOSITORY = "MKasumi1007/free-cross-device-audiobook"
BOOK_ID = "stage4-public-domain-smoke"


def synthetic_book() -> ParsedBook:
    segment = TextSegment(
        segment_id="stage4-synthetic-segment",
        chapter_id="stage4-synthetic-chapter",
        order=0,
        display_text="清晨的光落在项目自制的测试书页上。",
        spoken_text="清晨的光落在项目自制的测试书页上。",
        text_hash="a" * 64,
        kind=SegmentKind.PARAGRAPH,
    )
    return ParsedBook(
        book_id=BOOK_ID,
        title="Stage 4 Synthetic Browser Fixture",
        author="Project-created fixture",
        source_format="TXT",
        source_sha256="b" * 64,
        publication_mode=PublicationMode.PUBLIC_RIGHTS_CONFIRMED,
        chapters=(Chapter(
            chapter_id=segment.chapter_id,
            order=0,
            title="Synthetic Chapter",
            source_href="project-created",
            segments=(segment,),
        ),),
        rights_confirmed_at="2026-07-17T00:00:00+00:00",
    )


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    work = root / ".local/stage4-text-smoke"
    publisher = GitHubRepositoryAssetPublisher(REPOSITORY)
    asset = BookTextPublisher(work, publisher).publish(synthetic_book())
    timeline_path = work / "timeline.json.gz"
    timeline_payload = {
        "schema_version": 1,
        "book_id": BOOK_ID,
        "chunk_id": "stage4-synthetic-chunk",
        "chapter_id": "stage4-synthetic-chapter",
        "duration_seconds": 2.0,
        "segments": [{
            "segment_id": "stage4-synthetic-segment",
            "chapter_id": "stage4-synthetic-chapter",
            "segment_order": 0,
            "start_seconds": 0.0,
            "end_seconds": 2.0,
        }],
    }
    work.mkdir(parents=True, exist_ok=True)
    with timeline_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as archive:
            archive.write(json.dumps(timeline_payload, separators=(",", ":")).encode("utf-8"))
    timeline_hash = sha256(timeline_path.read_bytes()).hexdigest()
    timeline = publisher.publish(
        BOOK_ID,
        timeline_path,
        f"timeline-stage4-synthetic-chunk-{timeline_hash[:12]}.json.gz",
    )
    timeline_path.unlink(missing_ok=True)
    result = {
        "rights": "project-created synthetic fixture",
        "text": asdict(asset),
        "timeline": asdict(timeline),
    }
    work.mkdir(parents=True, exist_ok=True)
    (work / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "text_asset_id": asset.asset_id,
        "timeline_asset_id": timeline.asset_id,
        "verified": True,
    }))


if __name__ == "__main__":
    main()
