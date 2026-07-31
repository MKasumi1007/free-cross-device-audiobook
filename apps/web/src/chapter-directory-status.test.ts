import type { ParsedBook } from "@audiobook/contracts";
import { describe, expect, it } from "vitest";

import type { AudioChunk, GenerationQueueItem } from "./cloud";
import { chapterDirectoryStatus } from "./chapter-directory-status";

const book: ParsedBook = {
  book_id: "book-a",
  title: "目录状态测试",
  author: "测试",
  source_format: "TXT",
  source_sha256: "a".repeat(64),
  publication_mode: "LOCAL_ONLY",
  rights_confirmed_at: null,
  warnings: [],
  chapters: [{
    chapter_id: "chapter-a",
    title: "第一章",
    order: 0,
    source_href: "chapter-a.txt",
    segments: [{
      segment_id: "segment-a",
      chapter_id: "chapter-a",
      order: 0,
      kind: "PARAGRAPH",
      display_text: "第一段",
      spoken_text: "第一段",
      text_hash: "b".repeat(64),
    }],
  }],
};

function readyChunk(status: AudioChunk["status"] = "READY"): AudioChunk {
  return {
    owner_uid: "owner-a",
    task_id: "task-ready",
    book_id: "book-a",
    chunk_id: "chunk-a",
    chapter_id: "chapter-a",
    status,
    start_segment_id: "segment-a",
    end_segment_id: "segment-a",
    duration_seconds: 10,
    asset_id: 1,
    asset_url: "https://example.invalid/audio.m4a",
    sha256: "c".repeat(64),
    byte_size: 1_000,
    timeline_asset_id: 2,
    timeline_url: "https://example.invalid/timeline.json",
    timeline_sha256: "d".repeat(64),
    voice_version: "voice-a",
    deletion_generation: 0,
  };
}

function queueItem(
  status: GenerationQueueItem["status"],
  progressPercent: number,
): GenerationQueueItem {
  return {
    queue_id: "book-a:chapter-a",
    book_id: "book-a",
    chapter_id: "chapter-a",
    book_title: "目录状态测试",
    chapter_title: "第一章",
    status,
    priority: 1_000,
    task_ids: ["task-a"],
    total_chunks: 2,
    ready_chunks: progressPercent > 0 ? 1 : 0,
    pending_chunks: status === "COMPLETED" ? 0 : 1,
    estimated_seconds: 100,
    progress_percent: progressPercent,
    progress_stage: "",
    current_task_id: "",
    current_piece: 0,
    current_piece_total: 0,
    generated_audio_seconds: 0,
    elapsed_seconds: 0,
    eta_seconds: null,
    chapter_eta_seconds: null,
    historical_pause: false,
    local_only: false,
  };
}

describe("chapter directory status", () => {
  it("shows playable and generating at the same time for a partially generated chapter", () => {
    expect(chapterDirectoryStatus(
      book,
      book.chapters[0]!,
      [readyChunk()],
      [queueItem("GENERATING", 46.4)],
    )).toEqual({
      playable: true,
      playable_label: "可听 0:10",
      playable_seconds: 10,
      generation_label: "生成中 46%",
      generation_tone: "generating",
      progress_percent: 46,
    });
  });

  it("distinguishes queued, paused, and failed chapters", () => {
    expect(chapterDirectoryStatus(book, book.chapters[0]!, [], [queueItem("QUEUED", 0)]))
      .toMatchObject({ generation_label: "等待生成", generation_tone: "queued" });
    expect(chapterDirectoryStatus(book, book.chapters[0]!, [], [queueItem("PAUSED", 25)]))
      .toMatchObject({ generation_label: "已暂停", generation_tone: "paused" });
    expect(chapterDirectoryStatus(book, book.chapters[0]!, [], [queueItem("FAILED", 25)]))
      .toMatchObject({ generation_label: "待重试", generation_tone: "failed" });
  });

  it("does not claim a READY record is playable when its audio asset is missing", () => {
    const chunk = readyChunk();
    chunk.asset_url = null;
    expect(chapterDirectoryStatus(book, book.chapters[0]!, [chunk], []))
      .toMatchObject({
        playable: false,
        playable_label: "",
        playable_seconds: 0,
        generation_tone: "none",
      });
  });

  it("shows cleanup when a chapter has no remaining playable chunk", () => {
    expect(chapterDirectoryStatus(book, book.chapters[0]!, [readyChunk("DELETING")], []))
      .toMatchObject({ playable: false, generation_label: "音频清理中", generation_tone: "deleting" });
  });

  it("adds the duration of every genuinely playable chunk in the chapter", () => {
    const second = readyChunk();
    second.chunk_id = "chunk-b";
    second.duration_seconds = 65;
    expect(chapterDirectoryStatus(book, book.chapters[0]!, [readyChunk(), second], []))
      .toMatchObject({
        playable: true,
        playable_label: "可听 1:15",
        playable_seconds: 75,
      });
  });
});
