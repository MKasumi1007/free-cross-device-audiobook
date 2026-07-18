import { describe, expect, it } from "vitest";

import type { AudioChunk } from "./cloud";
import { demoBook } from "./demo";
import {
  chunkForSegment,
  firstPlayableSegmentIdForChapter,
  formatPlaybackTime,
  readyChunks,
  timelineSegmentAt,
} from "./player";

function chunk(
  chunkId: string,
  startSegmentId: string,
  endSegmentId: string,
  status: AudioChunk["status"] = "READY",
): AudioChunk {
  return {
    owner_uid: "owner-a",
    task_id: `task-${chunkId}`,
    book_id: demoBook.book_id,
    chunk_id: chunkId,
    chapter_id: demoBook.chapters[0]!.chapter_id,
    status,
    start_segment_id: startSegmentId,
    end_segment_id: endSegmentId,
    duration_seconds: 20,
    asset_id: 1,
    asset_url: `https://example.test/${chunkId}.m4a`,
    sha256: "a".repeat(64),
    byte_size: 100,
    timeline_asset_id: 2,
    timeline_url: `https://example.test/${chunkId}.json.gz`,
    timeline_sha256: "b".repeat(64),
    voice_version: "voice-a",
    deletion_generation: 0,
  };
}

describe("audiobook player planning", () => {
  it("sorts ready chunks by book order and finds the chunk for a segment", () => {
    const first = demoBook.chapters[0]!.segments[0]!.segment_id;
    const second = demoBook.chapters[0]!.segments[1]!.segment_id;
    const later = demoBook.chapters[1]!.segments[0]!.segment_id;
    const chunks = [
      chunk("later", later, later),
      chunk("failed", first, first, "FAILED_RETRYABLE"),
      chunk("first", first, second),
    ];
    expect(readyChunks(demoBook, chunks).map((item) => item.chunk_id)).toEqual(["first", "later"]);
    expect(chunkForSegment(demoBook, chunks, second)?.chunk_id).toBe("first");
  });

  it("treats an owner-only Firestore asset as playable without a public URL", () => {
    const first = demoBook.chapters[0]!.segments[0]!.segment_id;
    const privateChunk: AudioChunk = {
      ...chunk("private", first, first),
      asset_id: null,
      asset_url: null,
      timeline_asset_id: null,
      timeline_url: null,
      storage_mode: "PRIVATE_FIRESTORE",
      private_audio_key: "c".repeat(64),
      private_timeline_key: "d".repeat(64),
    };

    expect(readyChunks(demoBook, [privateChunk])).toEqual([privateChunk]);
  });

  it("opens a chapter at its first generated segment instead of its first text segment", () => {
    const chapter = demoBook.chapters[0]!;
    const generatedLater = chapter.segments[1]!.segment_id;

    expect(firstPlayableSegmentIdForChapter(
      demoBook,
      [chunk("partial", generatedLater, generatedLater)],
      chapter.chapter_id,
    )).toBe(generatedLater);
  });

  it("maps audio time to text and formats player time", () => {
    const segmentId = demoBook.chapters[0]!.segments[0]!.segment_id;
    const timeline = {
      schema_version: 1,
      book_id: demoBook.book_id,
      chunk_id: "chunk-a",
      chapter_id: demoBook.chapters[0]!.chapter_id,
      duration_seconds: 12,
      segments: [{
        segment_id: segmentId,
        chapter_id: demoBook.chapters[0]!.chapter_id,
        segment_order: 0,
        start_seconds: 0,
        end_seconds: 12,
      }],
    };
    expect(timelineSegmentAt(timeline, 4)?.segment_id).toBe(segmentId);
    expect(formatPlaybackTime(65.8)).toBe("01:05");
  });
});
