#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import subprocess
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from mac_agent.release_assets import GitHubReleasePublisher


REPOSITORY = "MKasumi1007/free-cross-device-audiobook"
BOOK_ID = "stage3-public-domain-smoke"


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    source = root / ".local/benchmark/qwen_output.wav"
    if not source.is_file():
        raise SystemExit("Stage 0 synthetic Qwen output is missing.")
    work = root / ".local/stage3-release-smoke"
    work.mkdir(parents=True, exist_ok=True)
    audio = work / "synthetic-qwen-smoke.m4a"
    encoded = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-movflags",
            "+faststart",
            str(audio),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if encoded.returncode != 0:
        raise SystemExit("Synthetic M4A encoding failed.")
    timeline = work / "synthetic-qwen-smoke.timeline.json.gz"
    with gzip.open(timeline, "wt", encoding="utf-8") as handle:
        json.dump(
            {
                "schema_version": 1,
                "book_id": BOOK_ID,
                "chunk_id": "synthetic-smoke-0001",
                "rights": "project-created synthetic fixture",
                "segments": [
                    {
                        "segment_id": "synthetic-segment-0001",
                        "start_seconds": 0,
                        "end_seconds": 7.68,
                    }
                ],
            },
            handle,
            separators=(",", ":"),
        )
    publisher = GitHubReleasePublisher(REPOSITORY)
    audio_asset = publisher.publish(
        BOOK_ID,
        audio,
        f"audio-synthetic-smoke-{file_hash(audio)[:12]}.m4a",
    )
    timeline_asset = publisher.publish(
        BOOK_ID,
        timeline,
        f"timeline-synthetic-smoke-{file_hash(timeline)[:12]}.json.gz",
    )
    result = {
        "rights": "project-created synthetic fixture",
        "audio": asdict(audio_asset),
        "timeline": asdict(timeline_asset),
    }
    (work / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "audio_asset_id": audio_asset.asset_id,
        "timeline_asset_id": timeline_asset.asset_id,
        "verified": True,
    }))


if __name__ == "__main__":
    main()
