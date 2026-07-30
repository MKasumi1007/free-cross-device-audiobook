import type { Chapter, ParsedBook } from "@audiobook/contracts";

import type { AudioChunk, GenerationQueueItem } from "./cloud";
import { readyChunks } from "./player";

export type ChapterGenerationTone =
  | "generating"
  | "queued"
  | "paused"
  | "failed"
  | "syncing"
  | "deleting"
  | "none";

export interface ChapterDirectoryStatus {
  playable: boolean;
  playable_label: string;
  playable_seconds: number;
  generation_label: string;
  generation_tone: ChapterGenerationTone;
  progress_percent: number | null;
}

function formatPlayableDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function roundedProgress(item: GenerationQueueItem): number {
  return Math.max(0, Math.min(100, Math.round(item.progress_percent)));
}

export function chapterDirectoryStatus(
  book: ParsedBook,
  chapter: Chapter,
  audioChunks: readonly AudioChunk[],
  generationQueue: readonly GenerationQueueItem[],
): ChapterDirectoryStatus {
  const segmentIds = new Set(chapter.segments.map((segment) => segment.segment_id));
  const playableChunks = readyChunks(book, [...audioChunks]).filter((chunk) => (
    chunk.chapter_id === chapter.chapter_id || segmentIds.has(chunk.start_segment_id)
  ));
  const playableSeconds = playableChunks.reduce(
    (total, chunk) => total + Math.max(0, Number(chunk.duration_seconds || 0)),
    0,
  );
  const playable = playableChunks.length > 0;
  const playableLabel = playable ? `可听 ${formatPlayableDuration(playableSeconds)}` : "";
  const queueItem = generationQueue.find((item) => (
    item.book_id === book.book_id && item.chapter_id === chapter.chapter_id
  ));
  const deleting = audioChunks.some((chunk) => (
    chunk.book_id === book.book_id
    && chunk.chapter_id === chapter.chapter_id
    && chunk.status === "DELETING"
  ));

  if (queueItem?.status === "GENERATING") {
    const progress = roundedProgress(queueItem);
    return {
      playable,
      playable_label: playableLabel,
      playable_seconds: playableSeconds,
      generation_label: `生成中 ${progress}%`,
      generation_tone: "generating",
      progress_percent: progress,
    };
  }
  if (queueItem?.status === "QUEUED") {
    const progress = roundedProgress(queueItem);
    return {
      playable,
      playable_label: playableLabel,
      playable_seconds: playableSeconds,
      generation_label: progress > 0 ? `等待生成 ${progress}%` : "等待生成",
      generation_tone: "queued",
      progress_percent: progress,
    };
  }
  if (queueItem?.status === "PAUSED") {
    return {
      playable,
      playable_label: playableLabel,
      playable_seconds: playableSeconds,
      generation_label: "已暂停",
      generation_tone: "paused",
      progress_percent: roundedProgress(queueItem),
    };
  }
  if (queueItem?.status === "FAILED") {
    return {
      playable,
      playable_label: playableLabel,
      playable_seconds: playableSeconds,
      generation_label: "待重试",
      generation_tone: "failed",
      progress_percent: roundedProgress(queueItem),
    };
  }
  if (queueItem?.status === "COMPLETED" && !playable) {
    return {
      playable: false,
      playable_label: "",
      playable_seconds: 0,
      generation_label: "音频同步中",
      generation_tone: "syncing",
      progress_percent: 100,
    };
  }
  if (deleting && !playable) {
    return {
      playable: false,
      playable_label: "",
      playable_seconds: 0,
      generation_label: "音频清理中",
      generation_tone: "deleting",
      progress_percent: null,
    };
  }
  return {
    playable,
    playable_label: playableLabel,
    playable_seconds: playableSeconds,
    generation_label: "",
    generation_tone: "none",
    progress_percent: null,
  };
}
