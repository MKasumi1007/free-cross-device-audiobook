import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AudioChunk } from "./cloud";
import { demoBook } from "./demo";
import { PlayerDock } from "./PlayerDock";
import { loadChunkTimeline } from "./player";

vi.mock("./player", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./player")>();
  return {
    ...actual,
    loadChunkTimeline: vi.fn(async (chunk: AudioChunk) => ({
      schema_version: 1,
      book_id: chunk.book_id,
      chunk_id: chunk.chunk_id,
      chapter_id: chunk.chapter_id,
      duration_seconds: 20,
      segments: [{
        segment_id: chunk.start_segment_id,
        chapter_id: chunk.chapter_id,
        segment_order: 0,
        start_seconds: 0,
        end_seconds: 20,
      }],
    })),
  };
});

function readyChunk(): AudioChunk {
  const segment = demoBook.chapters[0]!.segments[0]!;
  return {
    owner_uid: "owner-a",
    task_id: "task-a",
    book_id: demoBook.book_id,
    chunk_id: "chunk-a",
    chapter_id: segment.chapter_id,
    status: "READY",
    start_segment_id: segment.segment_id,
    end_segment_id: segment.segment_id,
    duration_seconds: 20,
    asset_id: 1,
    asset_url: "https://example.test/audio.m4a",
    sha256: "a".repeat(64),
    byte_size: 100,
    timeline_asset_id: 2,
    timeline_url: "https://example.test/timeline.json.gz",
    timeline_sha256: "b".repeat(64),
    voice_version: "voice-a",
    deletion_generation: 0,
  };
}

describe("PlayerDock", () => {
  beforeEach(() => {
    vi.mocked(loadChunkTimeline).mockClear();
    Object.defineProperty(HTMLMediaElement.prototype, "play", {
      configurable: true,
      value: vi.fn(async () => undefined),
    });
    Object.defineProperty(HTMLMediaElement.prototype, "pause", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("plays a ready chunk, highlights text, saves progress, and creates a bookmark", async () => {
    const chunk = readyChunk();
    const onHighlight = vi.fn();
    const onPosition = vi.fn();
    const onBookmark = vi.fn();
    const { container } = render(
      <PlayerDock
        book={demoBook}
        chunks={[chunk]}
        resumeSegmentId={chunk.start_segment_id}
        resumeOffsetSeconds={0}
        jumpRequest={null}
        macOnline
        onHighlight={onHighlight}
        onPosition={onPosition}
        onBookmark={onBookmark}
        onRepair={vi.fn()}
        onNotice={vi.fn()}
      />,
    );
    await waitFor(() => expect(loadChunkTimeline).toHaveBeenCalledWith(chunk, expect.any(AbortSignal)));
    const audio = container.querySelector("audio")!;
    Object.defineProperty(audio, "duration", { configurable: true, value: 20 });
    fireEvent.loadedMetadata(audio);
    fireEvent.click(screen.getByRole("button", { name: "播放" }));
    expect(audio.play).toHaveBeenCalled();

    audio.currentTime = 3;
    fireEvent.timeUpdate(audio);
    expect(onHighlight).toHaveBeenCalledWith(chunk.chapter_id, chunk.start_segment_id);
    expect(onPosition).toHaveBeenCalledWith(expect.objectContaining({
      segmentId: chunk.start_segment_id,
      audioOffsetSeconds: 3,
    }), false);

    fireEvent.click(screen.getByRole("button", { name: "书签" }));
    expect(onBookmark).toHaveBeenCalledWith(expect.objectContaining({ segmentId: chunk.start_segment_id }));
  });

  it("shows a clear waiting state when the Mac is offline and no audio exists", () => {
    render(
      <PlayerDock
        book={demoBook}
        chunks={[]}
        resumeSegmentId=""
        resumeOffsetSeconds={0}
        jumpRequest={null}
        macOnline={false}
        onHighlight={vi.fn()}
        onPosition={vi.fn()}
        onBookmark={vi.fn()}
        onRepair={vi.fn()}
        onNotice={vi.fn()}
      />,
    );
    expect(screen.getByText("等待 Mac 开机")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "播放" })).toBeDisabled();
  });
});
