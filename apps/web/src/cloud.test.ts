import type { ParsedBook } from "@audiobook/contracts";
import { describe, expect, it } from "vitest";

import {
  buildGenerationQueue,
  calculateAudioStats,
  planGenerationRequests,
} from "./cloud";

function longBook(): ParsedBook {
  return {
    book_id: "book-long",
    title: "长篇测试书",
    author: "测试",
    source_format: "TXT",
    source_sha256: "a".repeat(64),
    publication_mode: "PUBLIC_RIGHTS_CONFIRMED",
    rights_confirmed_at: "2026-07-17T00:00:00Z",
    warnings: [],
    chapters: [{
      chapter_id: "chapter-1",
      title: "第一章",
      order: 0,
      source_href: "chapter-1.txt",
      segments: Array.from({ length: 35 }, (_, index) => ({
        segment_id: `segment-${String(index).padStart(3, "0")}`,
        chapter_id: "chapter-1",
        order: index,
        kind: "PARAGRAPH",
        display_text: "字".repeat(2520),
        spoken_text: "字".repeat(2520),
        text_hash: String(index).padStart(64, "0"),
      })),
    }],
  };
}

describe("manual chapter generation queue", () => {
  it("plans every selected chapter chunk so the worker can enforce the cache limit", () => {
    const requests = planGenerationRequests(longBook(), "voice-a");
    expect(requests).toHaveLength(35);
    expect(requests[30]?.startSegmentId).toBe("segment-030");
  });

  it("records chapter identity and order on every planned chunk", () => {
    const requests = planGenerationRequests(longBook(), "voice-a");
    expect(requests[0]).toMatchObject({
      bookId: "book-long",
      bookTitle: "长篇测试书",
      chapterId: "chapter-1",
      chapterTitle: "第一章",
      chunkOrder: 0,
    });
    expect(requests[34]?.chunkOrder).toBe(34);
  });

  it("groups chunks into a persistent manual chapter queue", () => {
    const queue = buildGenerationQueue([
      {
        task_id: "chapter-1-b",
        book_id: "book-long",
        chapter_id: "chapter-1",
        chapter_title: "第一章",
        status: "QUEUED",
        priority: 999,
        chunk_order: 1,
        estimated_seconds: 600,
      },
      {
        task_id: "chapter-1-a",
        book_id: "book-long",
        chapter_id: "chapter-1",
        chapter_title: "第一章",
        status: "READY",
        priority: 1_000,
        chunk_order: 0,
        estimated_seconds: 600,
      },
      {
        task_id: "chapter-2-a",
        book_id: "book-long",
        chapter_id: "chapter-2",
        chapter_title: "第二章",
        status: "PAUSED",
        pause_reason: "USER_PAUSED",
        priority: 500,
        chunk_order: 0,
        estimated_seconds: 300,
      },
    ], [longBook()]);
    expect(queue).toHaveLength(2);
    expect(queue[0]).toMatchObject({
      queue_id: "book-long:chapter-1",
      status: "QUEUED",
      task_ids: ["chapter-1-a", "chapter-1-b"],
      ready_chunks: 1,
      pending_chunks: 1,
      estimated_seconds: 1_200,
    });
    expect(queue[1]).toMatchObject({
      queue_id: "book-long:chapter-2",
      status: "PAUSED",
    });
  });
});

describe("remote audio inventory", () => {
  it("counts active bytes without treating deletion history as occupied space", () => {
    expect(calculateAudioStats([
      { status: "READY", byte_size: 1_000, duration_seconds: 60 },
      { status: "DELETING", byte_size: 2_000, duration_seconds: 120 },
      { status: "DELETED", byte_size: 3_000, duration_seconds: 180 },
    ])).toEqual({
      chunks: 2,
      bytes: 3_000,
      duration_seconds: 180,
      deleting: 1,
      deleted: 1,
    });
  });
});
