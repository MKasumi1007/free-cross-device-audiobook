import { parsedBookSchema, type ParsedBook } from "@audiobook/contracts";
import {
  collection,
  doc,
  getDoc,
  getDocs,
  onSnapshot,
  query,
  runTransaction,
  serverTimestamp,
  setDoc,
  where,
  writeBatch,
  type DocumentReference,
  type DocumentData,
  type Unsubscribe,
} from "firebase/firestore";

import { getDeviceId } from "./device";
import { getFirebaseServices } from "./firebase";
import { classifyFirebaseError, type SyncError } from "./firebase-errors";
import { cloudSyncIsPaused, cloudSyncPauseMessage, recordEstimatedUsage } from "./usage";
import { decodeGzipJson, fetchVerifiedGzipJson, verifySha256 } from "./remote-asset";

const MAX_PRIVATE_ASSET_BYTES = 32 * 1024 * 1024;

export interface CloudBookSummary {
  book_id: string;
  title: string;
  author: string;
  source_format: "EPUB" | "TXT";
  source_sha256: string;
  publication_mode: "LOCAL_ONLY" | "PUBLIC_RIGHTS_CONFIRMED";
  chapter_count: number;
  segment_count: number;
  last_listened_at?: unknown;
  text_status?: "READY";
  text_asset_id?: number;
  text_asset_name?: string;
  text_asset_url?: string;
  text_sha256?: string;
  text_byte_size?: number;
  private_text_key?: string;
  private_text_name?: string;
  private_text_sha256?: string;
  private_text_byte_size?: number;
  private_text_parts?: number;
  updated_at?: unknown;
}

export interface AudioChunk {
  owner_uid: string;
  task_id: string;
  book_id: string;
  chunk_id: string;
  chapter_id: string;
  status: "READY" | "FAILED_RETRYABLE" | "DELETING" | "DELETED";
  start_segment_id: string;
  end_segment_id: string;
  duration_seconds: number;
  asset_id: number | null;
  asset_url: string | null;
  sha256: string;
  byte_size: number;
  timeline_asset_id: number | null;
  timeline_url: string | null;
  timeline_sha256: string;
  storage_mode?: "PUBLIC_GITHUB" | "PRIVATE_FIRESTORE";
  private_audio_key?: string | null;
  private_timeline_key?: string | null;
  private_audio_parts?: number;
  private_timeline_parts?: number;
  voice_version: string;
  deletion_generation: number;
  deletion_request_id?: string;
  delete_requested_at?: unknown;
  deleted_at?: unknown;
  completed_at?: unknown;
}

export interface AudioInventoryItem extends AudioChunk {
  book_title: string;
  chapter_title: string;
}

export interface AudioStats {
  chunks: number;
  bytes: number;
  duration_seconds: number;
  deleting: number;
  deleted: number;
}

export interface CloudBookmark {
  owner_uid: string;
  bookmark_id: string;
  book_id: string;
  chapter_id: string;
  segment_id: string;
  note: string;
  created_at: unknown;
}

export interface ProgressInput {
  book_id: string;
  chapter_id: string;
  segment_id: string;
  segment_order: number;
  audio_offset_seconds: number;
}

export interface CloudProgress extends ProgressInput {
  owner_uid: string;
  device_id: string;
  version: number;
  updated_at: unknown;
}

export interface ProgressSyncResult {
  status: "SAVED" | "CONFLICT";
  progress: CloudProgress;
}

export type GenerationTaskStatus =
  | "QUEUED"
  | "LEASED"
  | "GENERATING"
  | "ENCODING"
  | "UPLOADING"
  | "READY"
  | "PAUSED"
  | "FAILED_RETRYABLE"
  | "FAILED_FINAL"
  | "CANCELLED";

export interface GenerationTaskSummary {
  task_id: string;
  book_id: string;
  chapter_id?: string;
  book_title?: string;
  chapter_title?: string;
  status: GenerationTaskStatus;
  priority: number;
  pause_reason?: string | null;
  start_segment_id?: string;
  voice_version?: string;
  chunk_order?: number;
  estimated_seconds?: number;
  deletion_generation?: number;
  attempt_id?: number;
  progress_stage?: string;
  progress_completed_units?: number;
  progress_total_units?: number;
  progress_completed_segments?: number;
  progress_total_segments?: number;
  progress_current_segment_id?: string | null;
  progress_current_segment_order?: number;
  progress_current_piece?: number;
  progress_current_piece_total?: number;
  progress_generated_audio_seconds?: number;
  progress_elapsed_seconds?: number;
  progress_eta_seconds?: number | null;
  progress_started_at?: unknown;
  lease_deadline?: unknown;
}

export type GenerationQueueStatus =
  | "GENERATING"
  | "QUEUED"
  | "PAUSED"
  | "FAILED"
  | "COMPLETED"
  | "REMOVED";

export interface GenerationQueueItem {
  queue_id: string;
  book_id: string;
  chapter_id: string;
  book_title: string;
  chapter_title: string;
  status: GenerationQueueStatus;
  priority: number;
  task_ids: string[];
  total_chunks: number;
  ready_chunks: number;
  pending_chunks: number;
  estimated_seconds: number;
  progress_percent: number;
  progress_stage: string;
  current_task_id: string;
  current_piece: number;
  current_piece_total: number;
  generated_audio_seconds: number;
  elapsed_seconds: number;
  eta_seconds: number | null;
  chapter_eta_seconds: number | null;
  historical_pause: boolean;
}

export interface VoiceGenerationProfile {
  voice_version: string;
  confirmed: boolean;
}

const ACTIVE_GENERATION_STATUSES = new Set<GenerationTaskStatus>([
  "LEASED",
  "GENERATING",
  "ENCODING",
  "UPLOADING",
]);
const MANUAL_QUEUE_PRIORITY_START = 1_000_000;
const MANUAL_QUEUE_PRIORITY_STEP = 10_000;

function requireServices() {
  if (cloudSyncIsPaused()) {
    const error = new Error(cloudSyncPauseMessage()) as Error & { code?: string };
    error.code = "resource-exhausted-local-safety-pause";
    throw error;
  }
  const services = getFirebaseServices();
  if (!services) throw new Error("Firebase 尚未配置。");
  return services;
}

function requireDeletionServices() {
  const services = getFirebaseServices();
  if (!services) throw new Error("Firebase 尚未配置。");
  return services;
}

export function calculateAudioStats(chunks: readonly Pick<
  AudioChunk,
  "status" | "byte_size" | "duration_seconds"
>[]): AudioStats {
  return chunks.reduce<AudioStats>((stats, chunk) => {
    if (chunk.status === "DELETED") {
      stats.deleted += 1;
      return stats;
    }
    stats.chunks += 1;
    stats.bytes += Number(chunk.byte_size || 0);
    stats.duration_seconds += Number(chunk.duration_seconds || 0);
    if (chunk.status === "DELETING") stats.deleting += 1;
    return stats;
  }, { chunks: 0, bytes: 0, duration_seconds: 0, deleting: 0, deleted: 0 });
}

export async function loadAudioInventory(
  ownerUid: string,
  books: readonly (
    Pick<CloudBookSummary, "book_id" | "title">
    & { chapters?: readonly { chapter_id: string; title: string }[] }
  )[],
): Promise<AudioInventoryItem[]> {
  const { db } = requireServices();
  const inventory: AudioInventoryItem[] = [];
  for (const book of books) {
    const snapshot = await getDocs(collection(
      db,
      `users/${ownerUid}/books/${book.book_id}/audioChunks`,
    ));
    recordEstimatedUsage({ reads: snapshot.size });
    for (const item of snapshot.docs) {
      const chunk = item.data() as AudioChunk;
      inventory.push({
        ...chunk,
        book_title: book.title,
        chapter_title: book.chapters?.find(
          (chapter) => chapter.chapter_id === chunk.chapter_id,
        )?.title || chunk.chapter_id,
      });
    }
  }
  return inventory;
}

export async function syncBookMetadata(ownerUid: string, book: ParsedBook): Promise<void> {
  const { db } = requireServices();
  const bookReference = doc(db, `users/${ownerUid}/books/${book.book_id}`);
  const firstBatch = writeBatch(db);
  firstBatch.set(bookReference, {
    owner_uid: ownerUid,
    book_id: book.book_id,
    title: book.title,
    author: book.author,
    source_format: book.source_format,
    source_sha256: book.source_sha256,
    publication_mode: book.publication_mode,
    rights_confirmed_at: book.rights_confirmed_at,
    chapter_count: book.chapters.length,
    segment_count: book.chapters.reduce((count, chapter) => count + chapter.segments.length, 0),
    parser_version: 1,
    archived_at: null,
    last_listened_at: null,
    updated_at: serverTimestamp(),
  }, { merge: true });

  let batch = firstBatch;
  let writesInBatch = 1;
  let totalWrites = 1;
  for (const chapter of book.chapters) {
    if (writesInBatch >= 450) {
      await batch.commit();
      batch = writeBatch(db);
      writesInBatch = 0;
    }
    batch.set(doc(db, `users/${ownerUid}/books/${book.book_id}/chapters/${chapter.chapter_id}`), {
      owner_uid: ownerUid,
      book_id: book.book_id,
      chapter_id: chapter.chapter_id,
      order: chapter.order,
      title: chapter.title,
      source_href: chapter.source_href,
      segment_count: chapter.segments.length,
      first_segment_id: chapter.segments[0]?.segment_id || "",
      last_segment_id: chapter.segments.at(-1)?.segment_id || "",
      content_hash: chapter.segments.map((segment) => segment.text_hash).join("").slice(0, 256),
      updated_at: serverTimestamp(),
    });
    writesInBatch += 1;
    totalWrites += 1;
  }
  if (writesInBatch) await batch.commit();
  recordEstimatedUsage({ writes: totalWrites });
}

export function watchCloudBooks(
  ownerUid: string,
  onBooks: (books: CloudBookSummary[]) => void,
  onError: (error: SyncError) => void,
): Unsubscribe {
  const { db } = requireServices();
  return onSnapshot(collection(db, `users/${ownerUid}/books`), (snapshot) => {
    const books = snapshot.docs.map((item) => item.data() as CloudBookSummary);
    recordEstimatedUsage({ reads: snapshot.size });
    onBooks(books);
  }, (error) => onError(classifyFirebaseError(error)));
}

interface FirestoreByteValue {
  toUint8Array: () => Uint8Array;
}

function privatePayload(value: unknown): Uint8Array {
  if (value && typeof value === "object" && "toUint8Array" in value) {
    return (value as FirestoreByteValue).toUint8Array();
  }
  if (value instanceof Uint8Array) return value;
  throw new Error("PRIVATE_ASSET_PART_INVALID");
}

export async function loadPrivateAssetBytes(
  ownerUid: string,
  assetKey: string,
  expectedSha256: string,
  signal?: AbortSignal,
): Promise<Uint8Array<ArrayBuffer>> {
  if (!/^[a-f0-9]{64}$/.test(assetKey)) throw new Error("PRIVATE_ASSET_KEY_INVALID");
  const { db } = requireServices();
  const assetPath = `users/${ownerUid}/privateAssets/${assetKey}`;
  const metadataSnapshot = await getDoc(doc(db, assetPath));
  recordEstimatedUsage({ reads: 1 });
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  if (!metadataSnapshot.exists()) throw new Error("PRIVATE_ASSET_UNAVAILABLE");
  const metadata = metadataSnapshot.data() as {
    owner_uid?: string;
    status?: string;
    byte_size?: number;
    part_count?: number;
    sha256?: string;
  };
  const byteSize = Number(metadata.byte_size || 0);
  const partCount = Number(metadata.part_count || 0);
  if (
    metadata.owner_uid !== ownerUid
    || metadata.status !== "READY"
    || byteSize < 1
    || byteSize > MAX_PRIVATE_ASSET_BYTES
    || partCount < 1
    || partCount > 64
  ) {
    throw new Error("PRIVATE_ASSET_METADATA_INVALID");
  }
  const snapshot = await getDocs(collection(db, `${assetPath}/parts`));
  recordEstimatedUsage({ reads: snapshot.size });
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  const parts = snapshot.docs
    .map((item) => item.data() as { part_index?: number; payload?: unknown })
    .sort((left, right) => Number(left.part_index) - Number(right.part_index));
  if (parts.length !== partCount) throw new Error("PRIVATE_ASSET_PARTS_MISSING");
  const combined = new Uint8Array(new ArrayBuffer(byteSize));
  let offset = 0;
  for (let index = 0; index < parts.length; index += 1) {
    if (Number(parts[index]?.part_index) !== index) throw new Error("PRIVATE_ASSET_PARTS_INVALID");
    const bytes = privatePayload(parts[index]?.payload);
    if (offset + bytes.byteLength > combined.byteLength) throw new Error("PRIVATE_ASSET_SIZE_MISMATCH");
    combined.set(bytes, offset);
    offset += bytes.byteLength;
  }
  if (offset !== byteSize) throw new Error("PRIVATE_ASSET_SIZE_MISMATCH");
  await verifySha256(combined.buffer, expectedSha256 || String(metadata.sha256 || ""));
  return combined;
}

export async function loadRemoteBook(
  ownerUid: string,
  book: CloudBookSummary,
): Promise<ParsedBook> {
  if (book.text_status !== "READY") {
    throw new Error("这本书的手机正文还没有准备完成。");
  }
  let payload: unknown;
  if (book.publication_mode === "LOCAL_ONLY") {
    if (!book.private_text_key || !book.private_text_sha256) {
      throw new Error("这本私有书的正文还在准备，请等第一段音频开始生成后再试。");
    }
    const bytes = await loadPrivateAssetBytes(
      ownerUid,
      book.private_text_key,
      book.private_text_sha256,
    );
    payload = await decodeGzipJson(bytes.buffer);
  } else {
    if (!book.text_asset_url || !book.text_sha256) {
      throw new Error("这本书的手机正文还没有准备完成。");
    }
    payload = await fetchVerifiedGzipJson(book.text_asset_url, book.text_sha256);
  }
  return parsedBookSchema.parse(payload);
}

export function watchAudioChunks(
  ownerUid: string,
  bookId: string,
  onChunks: (chunks: AudioChunk[]) => void,
  onError: (error: SyncError) => void,
): Unsubscribe {
  const { db } = requireServices();
  return onSnapshot(collection(db, `users/${ownerUid}/books/${bookId}/audioChunks`), (snapshot) => {
    recordEstimatedUsage({ reads: snapshot.size });
    onChunks(snapshot.docs.map((item) => item.data() as AudioChunk));
  }, (error) => onError(classifyFirebaseError(error)));
}

export function watchBookmarks(
  ownerUid: string,
  bookId: string,
  onBookmarks: (bookmarks: CloudBookmark[]) => void,
  onError: (error: SyncError) => void,
): Unsubscribe {
  const { db } = requireServices();
  return onSnapshot(collection(db, `users/${ownerUid}/books/${bookId}/bookmarks`), (snapshot) => {
    recordEstimatedUsage({ reads: snapshot.size });
    onBookmarks(snapshot.docs.map((item) => item.data() as CloudBookmark));
  }, (error) => onError(classifyFirebaseError(error)));
}

function progressFromData(value: DocumentData): CloudProgress {
  return value as CloudProgress;
}

export async function loadCloudProgress(ownerUid: string, bookId: string): Promise<CloudProgress | null> {
  const { db } = requireServices();
  const snapshot = await getDoc(doc(db, `users/${ownerUid}/progress/${bookId}`));
  recordEstimatedUsage({ reads: 1 });
  return snapshot.exists() ? progressFromData(snapshot.data()) : null;
}

export async function saveProgressOptimistically(
  ownerUid: string,
  progress: ProgressInput,
  expectedVersion: number,
): Promise<ProgressSyncResult> {
  const { db } = requireServices();
  const reference = doc(db, `users/${ownerUid}/progress/${progress.book_id}`);
  const result = await runTransaction(db, async (transaction): Promise<ProgressSyncResult> => {
    const snapshot = await transaction.get(reference);
    const current = snapshot.exists() ? progressFromData(snapshot.data()) : null;
    const currentVersion = current?.version || 0;
    if (currentVersion !== expectedVersion && current) {
      return { status: "CONFLICT", progress: current };
    }
    const next: CloudProgress = {
      owner_uid: ownerUid,
      device_id: getDeviceId(),
      ...progress,
      version: currentVersion + 1,
      updated_at: serverTimestamp(),
    };
    transaction.set(reference, next);
    return { status: "SAVED", progress: next };
  });
  recordEstimatedUsage({ reads: 1, writes: result.status === "SAVED" ? 1 : 0 });
  return result;
}

export async function saveBookmark(
  ownerUid: string,
  bookId: string,
  chapterId: string,
  segmentId: string,
  note = "",
): Promise<string> {
  const { db } = requireServices();
  const bookmarkId = crypto.randomUUID();
  await setDoc(doc(db, `users/${ownerUid}/books/${bookId}/bookmarks/${bookmarkId}`), {
    owner_uid: ownerUid,
    bookmark_id: bookmarkId,
    book_id: bookId,
    chapter_id: chapterId,
    segment_id: segmentId,
    note: note.slice(0, 500),
    created_at: serverTimestamp(),
    updated_at: serverTimestamp(),
  });
  recordEstimatedUsage({ writes: 1 });
  return bookmarkId;
}

export async function saveVoiceGenerationProfile(
  ownerUid: string,
  voiceVersion: string,
): Promise<void> {
  if (!voiceVersion) return;
  const { db } = requireServices();
  await setDoc(doc(db, `users/${ownerUid}/voiceSettings/current`), {
    owner_uid: ownerUid,
    setting_id: "current",
    voice_version: voiceVersion,
    confirmed: true,
    updated_at: serverTimestamp(),
  }, { merge: true });
  recordEstimatedUsage({ writes: 1 });
}

export async function loadVoiceGenerationProfile(
  ownerUid: string,
): Promise<VoiceGenerationProfile | null> {
  const { db } = requireServices();
  const snapshot = await getDoc(doc(db, `users/${ownerUid}/voiceSettings/current`));
  recordEstimatedUsage({ reads: 1 });
  if (!snapshot.exists()) return null;
  const value = snapshot.data() as VoiceGenerationProfile;
  return value.confirmed && value.voice_version ? value : null;
}

export function watchGenerationTasks(
  ownerUid: string,
  onTasks: (tasks: GenerationTaskSummary[]) => void,
  onError: (error: SyncError) => void,
): Unsubscribe {
  const { db } = requireServices();
  return onSnapshot(collection(db, `users/${ownerUid}/generationRequests`), (snapshot) => {
    recordEstimatedUsage({ reads: snapshot.size });
    onTasks(snapshot.docs.map((item) => ({
      ...(item.data() as Omit<GenerationTaskSummary, "task_id">),
      task_id: String(item.data().task_id || item.id),
    })));
  }, (error) => onError(classifyFirebaseError(error)));
}

function chapterForTask(
  task: GenerationTaskSummary,
  books: readonly ParsedBook[],
): { chapter_id: string; book_title: string; chapter_title: string } {
  const book = books.find((item) => item.book_id === task.book_id);
  const chapter = book?.chapters.find((item) => (
    item.chapter_id === task.chapter_id
    || item.segments.some((segment) => segment.segment_id === task.start_segment_id)
  ));
  return {
    chapter_id: task.chapter_id || chapter?.chapter_id || "unknown",
    book_title: task.book_title || book?.title || "未载入的书",
    chapter_title: task.chapter_title || chapter?.title || "旧版待生成任务",
  };
}

function timestampMillis(value: unknown): number | null {
  if (value instanceof Date) return value.getTime();
  if (!value || typeof value !== "object") return null;
  const timestamp = value as {
    seconds?: number;
    nanoseconds?: number;
    toMillis?: () => number;
  };
  if (typeof timestamp.toMillis === "function") return timestamp.toMillis();
  if (typeof timestamp.seconds === "number") {
    return timestamp.seconds * 1000 + Number(timestamp.nanoseconds || 0) / 1_000_000;
  }
  return null;
}

export function generationTaskIsLive(task: GenerationTaskSummary): boolean {
  if (!ACTIVE_GENERATION_STATUSES.has(task.status)) return false;
  const deadline = timestampMillis(task.lease_deadline);
  return deadline == null || deadline > Date.now();
}

function queueStatus(tasks: readonly GenerationTaskSummary[]): GenerationQueueStatus {
  if (tasks.some(generationTaskIsLive)) return "GENERATING";
  if (tasks.some((task) => task.status === "FAILED_RETRYABLE" || task.status === "FAILED_FINAL")) {
    return "FAILED";
  }
  if (tasks.some((task) => task.status === "QUEUED")) return "QUEUED";
  if (tasks.some((task) => task.status === "PAUSED")) return "PAUSED";
  if (tasks.some((task) => ACTIVE_GENERATION_STATUSES.has(task.status))) return "QUEUED";
  if (tasks.every((task) => task.status === "READY")) return "COMPLETED";
  return "REMOVED";
}

export function buildGenerationQueue(
  tasks: readonly GenerationTaskSummary[],
  books: readonly ParsedBook[],
): GenerationQueueItem[] {
  const groups = new Map<string, {
    book_id: string;
    chapter_id: string;
    book_title: string;
    chapter_title: string;
    tasks: GenerationTaskSummary[];
  }>();
  for (const task of tasks) {
    if (!task.book_id) continue;
    const chapter = chapterForTask(task, books);
    const queueId = `${task.book_id}:${chapter.chapter_id}`;
    const current = groups.get(queueId) || {
      book_id: task.book_id,
      chapter_id: chapter.chapter_id,
      book_title: chapter.book_title,
      chapter_title: chapter.chapter_title,
      tasks: [],
    };
    current.tasks.push(task);
    groups.set(queueId, current);
  }
  return [...groups.entries()]
    .map(([queueId, group]): GenerationQueueItem => {
      const ordered = [...group.tasks].sort((left, right) => (
        Number(left.chunk_order || 0) - Number(right.chunk_order || 0)
        || left.task_id.localeCompare(right.task_id)
      ));
      const ready = ordered.filter((task) => task.status === "READY").length;
      const pending = ordered.filter((task) => (
        task.status !== "READY" && task.status !== "CANCELLED"
      )).length;
      const active = ordered
        .filter(generationTaskIsLive)
        .sort((left, right) => (
          Number(timestampMillis(right.lease_deadline) || 0)
          - Number(timestampMillis(left.lease_deadline) || 0)
        ))[0];
      const activeCompleted = Number(active?.progress_completed_units || 0);
      const activeTotal = Number(active?.progress_total_units || 0);
      const activeFraction = activeTotal > 0
        ? Math.min(1, Math.max(0, activeCompleted / activeTotal))
        : 0;
      const total = ordered.length;
      const progressPercent = total > 0
        ? Math.min(100, ((ready + activeFraction) / total) * 100)
        : 0;
      const activeEta = active?.progress_eta_seconds == null
        ? null
        : Math.max(0, Number(active.progress_eta_seconds));
      const generatedSeconds = Number(active?.progress_generated_audio_seconds || 0);
      const elapsedSeconds = Number(active?.progress_elapsed_seconds || 0);
      const realtimeFactor = generatedSeconds > 0 && elapsedSeconds > 0
        ? elapsedSeconds / generatedSeconds
        : null;
      const remainingAfterActive = ordered
        .filter((task) => (
          task.status !== "READY"
          && task.status !== "CANCELLED"
          && task.task_id !== active?.task_id
        ))
        .reduce((totalSeconds, task) => (
          totalSeconds + Number(task.estimated_seconds || 0)
        ), 0);
      const chapterEta = activeEta == null
        ? null
        : activeEta + (realtimeFactor == null ? 0 : remainingAfterActive * realtimeFactor);
      return {
        queue_id: queueId,
        book_id: group.book_id,
        chapter_id: group.chapter_id,
        book_title: group.book_title,
        chapter_title: group.chapter_title,
        status: queueStatus(ordered),
        priority: Math.max(...ordered.map((task) => Number(task.priority || 0))),
        task_ids: ordered.map((task) => task.task_id),
        total_chunks: ordered.length,
        ready_chunks: ready,
        pending_chunks: pending,
        estimated_seconds: ordered.reduce(
          (total, task) => total + Number(task.estimated_seconds || 0),
          0,
        ),
        progress_percent: progressPercent,
        progress_stage: String(active?.progress_stage || active?.status || ""),
        current_task_id: active?.task_id || "",
        current_piece: Number(active?.progress_current_piece || 0),
        current_piece_total: Number(active?.progress_current_piece_total || 0),
        generated_audio_seconds: generatedSeconds,
        elapsed_seconds: elapsedSeconds,
        eta_seconds: activeEta,
        chapter_eta_seconds: chapterEta,
        historical_pause: ordered.some((task) => (
          task.status === "PAUSED"
          && task.pause_reason === "USER_PAUSED"
          && Number(task.attempt_id || 0) > 0
          && !task.progress_stage
        )),
      };
    })
    .sort((left, right) => {
      if (left.status === "GENERATING" && right.status !== "GENERATING") return -1;
      if (right.status === "GENERATING" && left.status !== "GENERATING") return 1;
      const leftDone = left.status === "COMPLETED" || left.status === "REMOVED";
      const rightDone = right.status === "COMPLETED" || right.status === "REMOVED";
      if (leftDone !== rightDone) return leftDone ? 1 : -1;
      return right.priority - left.priority || left.queue_id.localeCompare(right.queue_id);
    });
}

export async function markBookListened(
  ownerUid: string,
  activeBookId: string,
): Promise<void> {
  const { db } = requireServices();
  await setDoc(doc(db, `users/${ownerUid}/books/${activeBookId}`), {
    last_listened_at: serverTimestamp(),
    updated_at: serverTimestamp(),
  }, { merge: true });
  recordEstimatedUsage({ writes: 1 });
}

export async function requestAudioRepair(ownerUid: string, chunk: AudioChunk): Promise<void> {
  const { db } = requireServices();
  const chunkReference = doc(
    db,
    `users/${ownerUid}/books/${chunk.book_id}/audioChunks/${chunk.chunk_id}`,
  );
  const taskReference = doc(db, `users/${ownerUid}/generationRequests/${chunk.task_id}`);
  await runTransaction(db, async (transaction) => {
    const [chunkSnapshot, taskSnapshot] = await Promise.all([
      transaction.get(chunkReference),
      transaction.get(taskReference),
    ]);
    if (!chunkSnapshot.exists() || !taskSnapshot.exists()) {
      throw new Error("这段音频记录不完整，请稍后再试。");
    }
    const currentChunk = chunkSnapshot.data() as AudioChunk;
    const deletionGeneration = Number(currentChunk.deletion_generation || 0) + 1;
    transaction.update(chunkReference, {
      status: "FAILED_RETRYABLE",
      error_code: "PLAYBACK_UNAVAILABLE",
      deletion_generation: deletionGeneration,
      updated_at: serverTimestamp(),
    });
    transaction.update(taskReference, {
      status: "QUEUED",
      pause_reason: null,
      priority: 300,
      deletion_generation: deletionGeneration,
      updated_at: serverTimestamp(),
    });
  });
  recordEstimatedUsage({ reads: 2, writes: 2 });
}

export function audioChunkCanBeDeleted(chunk: AudioChunk): boolean {
  return chunk.status === "READY" || chunk.status === "FAILED_RETRYABLE";
}

export async function requestAudioDeletion(
  ownerUid: string,
  chunks: readonly AudioChunk[],
): Promise<number> {
  const { db } = requireDeletionServices();
  let queued = 0;
  for (const chunk of chunks) {
    const requestId = crypto.randomUUID();
    const chunkReference = doc(
      db,
      `users/${ownerUid}/books/${chunk.book_id}/audioChunks/${chunk.chunk_id}`,
    );
    const taskReference = doc(db, `users/${ownerUid}/generationRequests/${chunk.task_id}`);
    const requestReference = doc(db, `users/${ownerUid}/audioDeletionRequests/${requestId}`);
    const created = await runTransaction(db, async (transaction) => {
      const [chunkSnapshot, taskSnapshot] = await Promise.all([
        transaction.get(chunkReference),
        transaction.get(taskReference),
      ]);
      if (!chunkSnapshot.exists()) return false;
      const current = chunkSnapshot.data() as AudioChunk;
      if (!audioChunkCanBeDeleted(current)) return false;
      const deletionGeneration = Number(current.deletion_generation || 0) + 1;
      transaction.update(chunkReference, {
        status: "DELETING",
        deletion_generation: deletionGeneration,
        deletion_request_id: requestId,
        delete_requested_at: serverTimestamp(),
        updated_at: serverTimestamp(),
      });
      if (taskSnapshot.exists()) {
        transaction.update(taskReference, {
          status: "CANCELLED",
          pause_reason: null,
          deletion_generation: deletionGeneration,
          updated_at: serverTimestamp(),
        });
      }
      transaction.set(requestReference, {
        owner_uid: ownerUid,
        request_id: requestId,
        book_id: current.book_id,
        chunk_id: current.chunk_id,
        task_id: current.task_id,
        asset_id: current.asset_id,
        asset_url: current.asset_url,
        timeline_asset_id: current.timeline_asset_id,
        timeline_url: current.timeline_url,
        storage_mode: current.storage_mode || "PUBLIC_GITHUB",
        private_audio_key: current.private_audio_key || null,
        private_timeline_key: current.private_timeline_key || null,
        private_audio_parts: current.private_audio_parts || 0,
        private_timeline_parts: current.private_timeline_parts || 0,
        deletion_generation: deletionGeneration,
        status: "QUEUED",
        attempt_count: 0,
        created_at: serverTimestamp(),
        updated_at: serverTimestamp(),
      });
      return true;
    });
    recordEstimatedUsage({ reads: 2, writes: created ? 3 : 0 });
    if (created) queued += 1;
  }
  return queued;
}

export interface BookDeletionResult {
  cancelled_tasks: number;
  queued_audio_chunks: number;
}

async function commitDeletes(
  references: readonly DocumentReference<DocumentData>[],
): Promise<number> {
  if (references.length === 0) return 0;
  const { db } = requireDeletionServices();
  let deleted = 0;
  for (let offset = 0; offset < references.length; offset += 400) {
    const batch = writeBatch(db);
    const page = references.slice(offset, offset + 400);
    page.forEach((reference) => batch.delete(reference));
    await batch.commit();
    deleted += page.length;
  }
  return deleted;
}

export async function requestBookDeletion(
  ownerUid: string,
  bookId: string,
): Promise<BookDeletionResult> {
  const { db } = requireDeletionServices();
  const bookReference = doc(db, `users/${ownerUid}/books/${bookId}`);
  const requestReference = doc(db, `users/${ownerUid}/bookDeletionRequests/${bookId}`);
  const [bookSnapshot, requestSnapshot] = await Promise.all([
    getDoc(bookReference),
    getDoc(requestReference),
  ]);
  recordEstimatedUsage({ reads: 2 });
  if (!bookSnapshot.exists()) throw new Error("云端已经找不到这本书，刷新书架后再试。");
  const book = bookSnapshot.data() as CloudBookSummary;
  const currentRequest = requestSnapshot.data() as { status?: string } | undefined;
  if (!requestSnapshot.exists()) {
    await setDoc(requestReference, {
      owner_uid: ownerUid,
      request_id: bookId,
      book_id: bookId,
      title: book.title,
      publication_mode: book.publication_mode,
      private_text_key: book.private_text_key || null,
      private_text_parts: Number(book.private_text_parts || 0),
      text_asset_id: book.text_asset_id ?? null,
      text_asset_url: book.text_asset_url || null,
      status: "PREPARING",
      attempt_count: 0,
      created_at: serverTimestamp(),
      updated_at: serverTimestamp(),
    });
    recordEstimatedUsage({ writes: 1 });
  } else if (currentRequest?.status === "DONE") {
    throw new Error("这本书已经完成永久删除，刷新书架即可。");
  }

  const taskSnapshot = await getDocs(query(
    collection(db, `users/${ownerUid}/generationRequests`),
    where("book_id", "==", bookId),
  ));
  recordEstimatedUsage({ reads: taskSnapshot.size });
  let cancelledTasks = 0;
  for (let offset = 0; offset < taskSnapshot.docs.length; offset += 400) {
    const batch = writeBatch(db);
    const page = taskSnapshot.docs.slice(offset, offset + 400);
    page.forEach((task) => {
      const status = String(task.data().status || "");
      if (status === "CANCELLED") return;
      batch.update(task.ref, {
        status: "CANCELLED",
        pause_reason: null,
        updated_at: serverTimestamp(),
      });
      cancelledTasks += 1;
    });
    if (page.some((task) => String(task.data().status || "") !== "CANCELLED")) {
      await batch.commit();
    }
  }
  recordEstimatedUsage({ writes: cancelledTasks });

  const audioSnapshot = await getDocs(collection(
    db,
    `users/${ownerUid}/books/${bookId}/audioChunks`,
  ));
  recordEstimatedUsage({ reads: audioSnapshot.size });
  const chunks = audioSnapshot.docs.map((item) => item.data() as AudioChunk);
  const queuedAudioChunks = await requestAudioDeletion(
    ownerUid,
    chunks.filter(audioChunkCanBeDeleted),
  );

  if (!requestSnapshot.exists() || currentRequest?.status === "PREPARING") {
    await runTransaction(db, async (transaction) => {
      const latest = await transaction.get(requestReference);
      if (!latest.exists()) throw new Error("永久删除请求没有保存成功。");
      if (latest.data().status === "PREPARING") {
        transaction.update(requestReference, {
          status: "QUEUED",
          updated_at: serverTimestamp(),
        });
      }
    });
    recordEstimatedUsage({ reads: 1, writes: 1 });
  }

  const [chapters, bookmarks] = await Promise.all([
    getDocs(collection(db, `users/${ownerUid}/books/${bookId}/chapters`)),
    getDocs(collection(db, `users/${ownerUid}/books/${bookId}/bookmarks`)),
  ]);
  recordEstimatedUsage({ reads: chapters.size + bookmarks.size });
  const childReferences = [
    ...chapters.docs.map((item) => item.ref),
    ...bookmarks.docs.map((item) => item.ref),
    doc(db, `users/${ownerUid}/progress/${bookId}`),
  ];
  const deletedChildren = await commitDeletes(childReferences);
  await commitDeletes([bookReference]);
  recordEstimatedUsage({ writes: deletedChildren + 1 });
  return {
    cancelled_tasks: cancelledTasks,
    queued_audio_chunks: queuedAudioChunks,
  };
}

export async function requestAudioRegeneration(
  ownerUid: string,
  chunk: AudioChunk,
): Promise<boolean> {
  const { db } = requireServices();
  const chunkReference = doc(
    db,
    `users/${ownerUid}/books/${chunk.book_id}/audioChunks/${chunk.chunk_id}`,
  );
  const taskReference = doc(db, `users/${ownerUid}/generationRequests/${chunk.task_id}`);
  const queued = await runTransaction(db, async (transaction) => {
    const [chunkSnapshot, taskSnapshot] = await Promise.all([
      transaction.get(chunkReference),
      transaction.get(taskReference),
    ]);
    if (!chunkSnapshot.exists() || !taskSnapshot.exists()) {
      throw new Error("这段音频缺少原始任务记录，暂时不能重新生成。");
    }
    const current = chunkSnapshot.data() as AudioChunk;
    if (current.status !== "DELETED") return false;
    transaction.update(chunkReference, {
      status: "FAILED_RETRYABLE",
      error_code: "REGENERATION_REQUESTED",
      updated_at: serverTimestamp(),
    });
    transaction.update(taskReference, {
      status: "QUEUED",
      pause_reason: null,
      priority: 300,
      deletion_generation: current.deletion_generation,
      start_segment_id: current.start_segment_id,
      retry_not_before: null,
      updated_at: serverTimestamp(),
    });
    return true;
  });
  recordEstimatedUsage({ reads: 2, writes: queued ? 2 : 0 });
  return queued;
}

export interface PlannedRequest {
  taskId: string;
  startSegmentId: string;
  priority: number;
  estimatedSeconds: number;
  bookId: string;
  bookTitle: string;
  chapterId: string;
  chapterTitle: string;
  chunkOrder: number;
}

export function planGenerationRequests(book: ParsedBook, voiceVersion: string): PlannedRequest[] {
  const requests: PlannedRequest[] = [];
  for (const chapter of book.chapters) {
    let chunkSeconds = 0;
    let chunkStart = "";
    let chunkOrder = 0;
    for (const segment of chapter.segments) {
      if (!segment.spoken_text) continue;
      const seconds = segment.spoken_text.replace(/\s/g, "").length / 4.2;
      if (!chunkStart) chunkStart = segment.segment_id;
      if (chunkSeconds > 0 && chunkSeconds + seconds > 600) {
        const taskId = `chunk-${book.book_id}-${voiceVersion}-${chunkStart}`;
        requests.push({
          taskId,
          startSegmentId: chunkStart,
          priority: requests.length ? 100 : 300,
          estimatedSeconds: chunkSeconds,
          bookId: book.book_id,
          bookTitle: book.title,
          chapterId: chapter.chapter_id,
          chapterTitle: chapter.title,
          chunkOrder,
        });
        chunkOrder += 1;
        chunkStart = segment.segment_id;
        chunkSeconds = 0;
      }
      chunkSeconds += seconds;
    }
    if (chunkStart) {
      const taskId = `chunk-${book.book_id}-${voiceVersion}-${chunkStart}`;
      requests.push({
        taskId,
        startSegmentId: chunkStart,
        priority: requests.length ? 100 : 300,
        estimatedSeconds: chunkSeconds,
        bookId: book.book_id,
        bookTitle: book.title,
        chapterId: chapter.chapter_id,
        chapterTitle: chapter.title,
        chunkOrder,
      });
    }
  }
  return requests;
}

export interface ChapterGenerationSelection {
  book: ParsedBook;
  chapter_ids: string[];
}

export interface EnqueueGenerationResult {
  chapters: number;
  created: number;
  resumed: number;
  unchanged: number;
}

function taskPayload(
  ownerUid: string,
  request: PlannedRequest,
  voiceVersion: string,
  storageMode: "PUBLIC_GITHUB" | "PRIVATE_FIRESTORE",
  priority: number,
) {
  return {
    owner_uid: ownerUid,
    task_id: request.taskId,
    book_id: request.bookId,
    book_title: request.bookTitle,
    chapter_id: request.chapterId,
    chapter_title: request.chapterTitle,
    chunk_order: request.chunkOrder,
    estimated_seconds: request.estimatedSeconds,
    status: "QUEUED",
    priority,
    attempt_id: 0,
    deletion_generation: 0,
    start_segment_id: request.startSegmentId,
    target_seconds: 600,
    chunk_seconds: 600,
    voice_version: voiceVersion,
    storage_mode: storageMode,
    created_at: serverTimestamp(),
    updated_at: serverTimestamp(),
  };
}

export async function enqueueGenerationChapters(
  ownerUid: string,
  selections: readonly ChapterGenerationSelection[],
  voiceVersion: string,
): Promise<EnqueueGenerationResult> {
  if (!voiceVersion) throw new Error("请先在 Mac 设置并确认你的声音。");
  const selected = selections.flatMap(({ book, chapter_ids: chapterIds }) => {
    const selectedIds = new Set(chapterIds);
    return planGenerationRequests(book, voiceVersion)
      .filter((request) => selectedIds.has(request.chapterId))
      .map((request) => ({
        request,
        storageMode: book.publication_mode === "LOCAL_ONLY"
          ? "PRIVATE_FIRESTORE" as const
          : "PUBLIC_GITHUB" as const,
      }));
  });
  const chapterIds = new Set(selected.map(({ request }) => (
    `${request.bookId}:${request.chapterId}`
  )));
  if (!selected.length) throw new Error("请至少选择一个有正文的章节。");

  const { db } = requireServices();
  const taskCollection = collection(db, `users/${ownerUid}/generationRequests`);
  const existingSnapshot = await getDocs(taskCollection);
  recordEstimatedUsage({ reads: existingSnapshot.size });
  const existing = new Map(existingSnapshot.docs.map((item) => [
    item.id,
    item.data() as GenerationTaskSummary,
  ]));
  const activePriorities = existingSnapshot.docs
    .map((item) => item.data() as GenerationTaskSummary)
    .filter((task) => (
      task.status !== "READY"
      && task.status !== "CANCELLED"
      && !chapterIds.has(`${task.book_id}:${task.chapter_id || ""}`)
    ))
    .map((task) => Number(task.priority || 0));
  const appendPriority = activePriorities.length
    ? Math.min(...activePriorities) - MANUAL_QUEUE_PRIORITY_STEP
    : MANUAL_QUEUE_PRIORITY_START;
  const chapterRanks = new Map<string, number>();
  let created = 0;
  let resumed = 0;
  let unchanged = 0;

  for (const { request, storageMode } of selected) {
    const chapterKey = `${request.bookId}:${request.chapterId}`;
    if (!chapterRanks.has(chapterKey)) chapterRanks.set(chapterKey, chapterRanks.size);
    const priority = appendPriority
      - Number(chapterRanks.get(chapterKey)) * MANUAL_QUEUE_PRIORITY_STEP
      - request.chunkOrder;
    const reference = doc(taskCollection, request.taskId);
    const previous = existing.get(request.taskId);
    if (!previous) {
      const wasCreated = await runTransaction(db, async (transaction) => {
        const current = await transaction.get(reference);
        if (current.exists()) return false;
        transaction.set(reference, taskPayload(
          ownerUid,
          request,
          voiceVersion,
          storageMode,
          priority,
        ));
        return true;
      });
      if (wasCreated) created += 1;
      else unchanged += 1;
      continue;
    }
    if (["PAUSED", "FAILED_RETRYABLE", "FAILED_FINAL", "CANCELLED"].includes(previous.status)) {
      await setDoc(reference, {
        status: "QUEUED",
        pause_reason: null,
        retry_not_before: null,
        priority,
        updated_at: serverTimestamp(),
      }, { merge: true });
      resumed += 1;
      continue;
    }
    unchanged += 1;
  }
  recordEstimatedUsage({ reads: selected.length, writes: created + resumed });
  return { chapters: chapterIds.size, created, resumed, unchanged };
}

export type GenerationQueueAction = "PAUSE" | "RESUME" | "REMOVE";

export async function updateGenerationQueueItem(
  ownerUid: string,
  taskIds: readonly string[],
  action: GenerationQueueAction,
): Promise<number> {
  const { db } = requireServices();
  let updated = 0;
  for (const taskId of taskIds) {
    const reference = doc(db, `users/${ownerUid}/generationRequests/${taskId}`);
    const changed = await runTransaction(db, async (transaction) => {
      const snapshot = await transaction.get(reference);
      if (!snapshot.exists()) return false;
      const task = snapshot.data() as GenerationTaskSummary;
      if (task.status === "READY" || ACTIVE_GENERATION_STATUSES.has(task.status)) return false;
      if (action === "PAUSE") {
        if (!["QUEUED", "FAILED_RETRYABLE", "FAILED_FINAL"].includes(task.status)) return false;
        transaction.update(reference, {
          status: "PAUSED",
          pause_reason: "USER_PAUSED",
          retry_not_before: null,
          updated_at: serverTimestamp(),
        });
        return true;
      }
      if (action === "RESUME") {
        if (!["PAUSED", "FAILED_RETRYABLE", "FAILED_FINAL", "CANCELLED"].includes(task.status)) {
          return false;
        }
        transaction.update(reference, {
          status: "QUEUED",
          pause_reason: null,
          retry_not_before: null,
          updated_at: serverTimestamp(),
        });
        return true;
      }
      if (!["QUEUED", "PAUSED", "FAILED_RETRYABLE", "FAILED_FINAL"].includes(task.status)) {
        return false;
      }
      transaction.update(reference, {
        status: "CANCELLED",
        pause_reason: null,
        retry_not_before: null,
        updated_at: serverTimestamp(),
      });
      return true;
    });
    if (changed) updated += 1;
  }
  recordEstimatedUsage({ reads: taskIds.length, writes: updated });
  return updated;
}

export async function reorderGenerationQueue(
  ownerUid: string,
  orderedItems: readonly Pick<GenerationQueueItem, "task_ids">[],
): Promise<void> {
  const { db } = requireServices();
  let reads = 0;
  let writes = 0;
  for (let rank = 0; rank < orderedItems.length; rank += 1) {
    const item = orderedItems[rank];
    for (let chunkOrder = 0; chunkOrder < item.task_ids.length; chunkOrder += 1) {
      const taskId = item.task_ids[chunkOrder];
      const reference = doc(db, `users/${ownerUid}/generationRequests/${taskId}`);
      const changed = await runTransaction(db, async (transaction) => {
        const snapshot = await transaction.get(reference);
        if (!snapshot.exists()) return false;
        const task = snapshot.data() as GenerationTaskSummary;
        if (task.status !== "QUEUED" && task.status !== "PAUSED") return false;
        transaction.update(reference, {
          priority: MANUAL_QUEUE_PRIORITY_START
            - rank * MANUAL_QUEUE_PRIORITY_STEP
            - chunkOrder,
          updated_at: serverTimestamp(),
        });
        return true;
      });
      reads += 1;
      if (changed) writes += 1;
    }
  }
  recordEstimatedUsage({ reads, writes });
}
