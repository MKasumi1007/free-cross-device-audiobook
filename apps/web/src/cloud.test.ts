import type { ParsedBook } from "@audiobook/contracts";
import { describe, expect, it } from "vitest";

import { generationTaskUpdate, planGenerationRequests, selectNextFiveHours } from "./cloud";

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

describe("five-hour generation queue", () => {
  it("selects the next five hours after an existing first batch", () => {
    const requests = planGenerationRequests(longBook(), "voice-a");
    const first = selectNextFiveHours(requests, new Set());
    expect(first).toHaveLength(30);

    const second = selectNextFiveHours(requests, new Set(first.map((item) => item.taskId)));
    expect(second).toHaveLength(5);
    expect(second[0]?.startSegmentId).toBe("segment-030");
  });

  it("prioritizes the open book and pauses only books inactive for 48 hours", () => {
    const inactive = new Set(["book-old"]);
    expect(generationTaskUpdate(
      { book_id: "book-active", status: "QUEUED", priority: 100 },
      "book-active",
      inactive,
    )).toEqual({ priority: 300 });
    expect(generationTaskUpdate(
      { book_id: "book-old", status: "QUEUED", priority: 100 },
      "book-active",
      inactive,
    )).toEqual({ status: "PAUSED", pause_reason: "INACTIVE_48_HOURS", priority: 100 });
    expect(generationTaskUpdate(
      { book_id: "book-active", status: "PAUSED", pause_reason: "INACTIVE_48_HOURS" },
      "book-active",
      inactive,
    )).toEqual({ status: "QUEUED", pause_reason: null, priority: 300 });
    expect(generationTaskUpdate(
      { book_id: "book-active", status: "FAILED_RETRYABLE", priority: 100 },
      "book-active",
      inactive,
    )).toEqual({ status: "QUEUED", pause_reason: null, priority: 300 });
    expect(generationTaskUpdate(
      { book_id: "book-old", status: "FAILED_RETRYABLE", priority: 300 },
      "book-active",
      inactive,
    )).toEqual({ status: "PAUSED", pause_reason: "INACTIVE_48_HOURS", priority: 100 });
  });
});
