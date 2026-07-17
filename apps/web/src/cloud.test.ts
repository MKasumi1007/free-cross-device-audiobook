import type { ParsedBook } from "@audiobook/contracts";
import { describe, expect, it } from "vitest";

import { planGenerationRequests, selectNextFiveHours } from "./cloud";

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
});
