import type { ParsedBook } from "@audiobook/contracts";
import { describe, expect, it } from "vitest";

import {
  buildGenerationQueue,
  calculateAudioStats,
  generationTaskIsLive,
  mergeAudioChunks,
  mergeGenerationTasks,
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

  it("combines worker checkpoints into visible chapter progress and ETA", () => {
    const queue = buildGenerationQueue([
      {
        task_id: "ready",
        book_id: "book-long",
        chapter_id: "chapter-1",
        status: "READY",
        priority: 1_000,
        chunk_order: 0,
        estimated_seconds: 600,
      },
      {
        task_id: "active",
        book_id: "book-long",
        chapter_id: "chapter-1",
        status: "GENERATING",
        priority: 999,
        chunk_order: 1,
        estimated_seconds: 600,
        progress_stage: "GENERATING",
        progress_completed_units: 2,
        progress_total_units: 4,
        progress_current_piece: 2,
        progress_current_piece_total: 4,
        progress_generated_audio_seconds: 50,
        progress_elapsed_seconds: 100,
        progress_eta_seconds: 50,
      },
      {
        task_id: "next",
        book_id: "book-long",
        chapter_id: "chapter-1",
        status: "QUEUED",
        priority: 998,
        chunk_order: 2,
        estimated_seconds: 600,
      },
    ], [longBook()]);

    expect(queue[0]).toMatchObject({
      status: "GENERATING",
      progress_percent: 50,
      progress_stage: "GENERATING",
      current_piece: 2,
      current_piece_total: 4,
      eta_seconds: 50,
      chapter_eta_seconds: 1_250,
    });
  });

  it("does not present an expired worker lease as a frozen active task", () => {
    const queue = buildGenerationQueue([
      {
        task_id: "expired",
        book_id: "book-long",
        chapter_id: "chapter-1",
        status: "GENERATING",
        priority: 1_000,
        chunk_order: 0,
        estimated_seconds: 600,
        lease_deadline: new Date(Date.now() - 60_000),
        progress_completed_units: 3,
        progress_total_units: 10,
      },
    ], [longBook()]);

    expect(queue[0]).toMatchObject({
      status: "QUEUED",
      current_task_id: "",
      progress_percent: 0,
    });
  });

  it("treats a live generation lease as proof that the Mac is online", () => {
    expect(generationTaskIsLive({
      task_id: "active",
      book_id: "book-long",
      status: "UPLOADING",
      priority: 1_000,
      lease_deadline: new Date(Date.now() + 60_000),
    })).toBe(true);
    expect(generationTaskIsLive({
      task_id: "expired",
      book_id: "book-long",
      status: "GENERATING",
      priority: 1_000,
      lease_deadline: new Date(Date.now() - 60_000),
    })).toBe(false);
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

describe("local generation fallback", () => {
  it("prefers local progress until the same task has synced to cloud", () => {
    const cloud = {
      task_id: "task-1",
      book_id: "book-long",
      chapter_id: "chapter-1",
      status: "QUEUED" as const,
      priority: 1_000,
    };
    const local = {
      ...cloud,
      owner_uid: "owner-1",
      status: "READY" as const,
      execution_mode: "LOCAL" as const,
      sync_status: "PENDING" as const,
    };

    expect(mergeGenerationTasks([cloud], [local])).toEqual([local]);
    expect(buildGenerationQueue([local], [longBook()])[0]).toMatchObject({
      status: "COMPLETED",
      local_only: true,
    });
    expect(mergeGenerationTasks([cloud], [{ ...local, sync_status: "SYNCED" }])).toEqual([cloud]);
  });

  it("keeps a loopback audio URL while local audio is waiting for sync", () => {
    const base = {
      owner_uid: "owner-1",
      task_id: "task-1",
      book_id: "book-long",
      chunk_id: "chunk-1",
      chapter_id: "chapter-1",
      status: "READY" as const,
      start_segment_id: "segment-000",
      end_segment_id: "segment-001",
      duration_seconds: 60,
      asset_id: 1,
      asset_url: "https://example.invalid/audio.m4a",
      sha256: "a".repeat(64),
      byte_size: 100,
      timeline_asset_id: 2,
      timeline_url: "https://example.invalid/timeline.json.gz",
      timeline_sha256: "b".repeat(64),
      voice_version: "voice-1",
      deletion_generation: 0,
    };
    const local = {
      ...base,
      asset_url: "http://127.0.0.1:17832/v1/local-generation/assets/task-1/audio",
      storage_mode: "LOCAL_MAC" as const,
      execution_mode: "LOCAL" as const,
      sync_status: "PENDING" as const,
    };

    expect(mergeAudioChunks([base], [local])).toEqual([local]);
    expect(mergeAudioChunks([base], [{ ...local, sync_status: "SYNCED" }])).toEqual([base]);
  });

  it("orders hybrid chapters by their unfinished local work, not old ready chunks", () => {
    const queue = buildGenerationQueue([
      {
        task_id: "chapter-1-ready-cloud",
        book_id: "book-long",
        chapter_id: "chapter-1",
        status: "READY",
        priority: 99_000,
        chunk_order: 0,
      },
      {
        task_id: "chapter-1-pending-local",
        book_id: "book-long",
        chapter_id: "chapter-1",
        status: "QUEUED",
        priority: 100,
        chunk_order: 1,
        execution_mode: "LOCAL",
        sync_status: "PENDING",
      },
      {
        task_id: "chapter-2-pending-local",
        book_id: "book-long",
        chapter_id: "chapter-2",
        status: "QUEUED",
        priority: 200,
        chunk_order: 0,
        execution_mode: "LOCAL",
        sync_status: "PENDING",
      },
    ], [longBook()]);

    expect(queue.map((item) => item.queue_id)).toEqual([
      "book-long:chapter-2",
      "book-long:chapter-1",
    ]);
  });
});
