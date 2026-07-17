import type { ParsedBook, TextSegment } from "@audiobook/contracts";

import type { AudioChunk } from "./cloud";
import { fetchVerifiedGzipJson } from "./remote-asset";

export interface TimelineSegment {
  segment_id: string;
  chapter_id: string;
  segment_order: number;
  start_seconds: number;
  end_seconds: number;
}

export interface ChunkTimeline {
  schema_version: number;
  book_id: string;
  chunk_id: string;
  chapter_id: string;
  duration_seconds: number;
  segments: TimelineSegment[];
}

function segmentIndexes(book: ParsedBook): Map<string, number> {
  const indexes = new Map<string, number>();
  let index = 0;
  for (const chapter of book.chapters) {
    for (const segment of chapter.segments) {
      indexes.set(segment.segment_id, index);
      index += 1;
    }
  }
  return indexes;
}

export function readyChunks(book: ParsedBook, chunks: AudioChunk[]): AudioChunk[] {
  const indexes = segmentIndexes(book);
  return chunks
    .filter((chunk) => chunk.status === "READY" && Boolean(chunk.asset_url))
    .sort((left, right) => (
      (indexes.get(left.start_segment_id) ?? Number.MAX_SAFE_INTEGER)
      - (indexes.get(right.start_segment_id) ?? Number.MAX_SAFE_INTEGER)
    ));
}

export function chunkForSegment(
  book: ParsedBook,
  chunks: AudioChunk[],
  segmentId: string,
): AudioChunk | undefined {
  const indexes = segmentIndexes(book);
  const target = indexes.get(segmentId);
  if (target === undefined) return undefined;
  return readyChunks(book, chunks).find((chunk) => {
    const start = indexes.get(chunk.start_segment_id);
    const end = indexes.get(chunk.end_segment_id);
    return start !== undefined && end !== undefined && start <= target && target <= end;
  });
}

export function textSegmentById(book: ParsedBook, segmentId: string): TextSegment | undefined {
  for (const chapter of book.chapters) {
    const segment = chapter.segments.find((item) => item.segment_id === segmentId);
    if (segment) return segment;
  }
  return undefined;
}

export function timelineSegmentAt(
  timeline: ChunkTimeline | null,
  seconds: number,
): TimelineSegment | undefined {
  if (!timeline) return undefined;
  return timeline.segments.find((segment) => (
    segment.start_seconds <= seconds && seconds < segment.end_seconds
  )) ?? timeline.segments.at(-1);
}

export async function loadChunkTimeline(
  chunk: AudioChunk,
  signal?: AbortSignal,
): Promise<ChunkTimeline> {
  if (!chunk.timeline_url) throw new Error("TIMELINE_MISSING");
  const timeline = await fetchVerifiedGzipJson(
    chunk.timeline_url,
    chunk.timeline_sha256,
    signal,
  ) as ChunkTimeline;
  if (timeline.book_id !== chunk.book_id || timeline.chunk_id !== chunk.chunk_id) {
    throw new Error("TIMELINE_ID_MISMATCH");
  }
  return timeline;
}

export function formatPlaybackTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
  const rounded = Math.floor(seconds);
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}
