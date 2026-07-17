import { parsedBookSchema, type ParsedBook } from "@audiobook/contracts";
import {
  collection,
  doc,
  getDoc,
  getDocs,
  onSnapshot,
  runTransaction,
  serverTimestamp,
  setDoc,
  writeBatch,
  type DocumentData,
  type Unsubscribe,
} from "firebase/firestore";

import { getDeviceId } from "./device";
import { getFirebaseServices } from "./firebase";
import { classifyFirebaseError, type SyncError } from "./firebase-errors";
import { recordEstimatedUsage } from "./usage";
import { fetchVerifiedGzipJson } from "./remote-asset";

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
  asset_id: number;
  asset_url: string;
  sha256: string;
  byte_size: number;
  timeline_asset_id: number;
  timeline_url: string;
  timeline_sha256: string;
  voice_version: string;
  deletion_generation: number;
  completed_at?: unknown;
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

function requireServices() {
  const services = getFirebaseServices();
  if (!services) throw new Error("Firebase 尚未配置。");
  return services;
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

export async function loadRemoteBook(book: CloudBookSummary): Promise<ParsedBook> {
  if (
    book.publication_mode !== "PUBLIC_RIGHTS_CONFIRMED"
    || book.text_status !== "READY"
    || !book.text_asset_url
    || !book.text_sha256
  ) {
    throw new Error("这本书的手机正文还没有准备完成。");
  }
  const payload = await fetchVerifiedGzipJson(book.text_asset_url, book.text_sha256);
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

function timestampMillis(value: unknown): number | null {
  if (!value || typeof value !== "object" || !("toMillis" in value)) return null;
  const toMillis = (value as { toMillis?: unknown }).toMillis;
  return typeof toMillis === "function" ? Number(toMillis.call(value)) : null;
}

export interface GenerationTaskSummary {
  book_id?: string;
  status?: string;
  priority?: number;
  pause_reason?: string | null;
}

export function generationTaskUpdate(
  task: GenerationTaskSummary,
  activeBookId: string,
  inactiveBookIds: ReadonlySet<string>,
): Record<string, unknown> | null {
  if (task.book_id === activeBookId) {
    if (task.status === "PAUSED" && task.pause_reason === "INACTIVE_48_HOURS") {
      return { status: "QUEUED", pause_reason: null, priority: 300 };
    }
    if (task.status === "FAILED_RETRYABLE") {
      return { status: "QUEUED", pause_reason: null, priority: 300 };
    }
    if (task.status === "QUEUED" && task.priority !== 300) return { priority: 300 };
    return null;
  }
  if (
    task.book_id
    && inactiveBookIds.has(task.book_id)
    && (task.status === "QUEUED" || task.status === "FAILED_RETRYABLE")
  ) {
    return { status: "PAUSED", pause_reason: "INACTIVE_48_HOURS", priority: 100 };
  }
  if (task.status === "QUEUED" && task.priority !== 100) return { priority: 100 };
  return null;
}

export async function prioritizeActiveBook(
  ownerUid: string,
  activeBookId: string,
  books: CloudBookSummary[],
): Promise<void> {
  const { db } = requireServices();
  await setDoc(doc(db, `users/${ownerUid}/books/${activeBookId}`), {
    last_listened_at: serverTimestamp(),
    updated_at: serverTimestamp(),
  }, { merge: true });
  const inactiveBookIds = new Set(
    books
      .filter((book) => {
        const lastListened = timestampMillis(book.last_listened_at);
        return book.book_id !== activeBookId
          && lastListened !== null
          && lastListened < Date.now() - 48 * 60 * 60 * 1000;
      })
      .map((book) => book.book_id),
  );
  const snapshot = await getDocs(collection(db, `users/${ownerUid}/generationRequests`));
  const updates: Array<{ reference: ReturnType<typeof doc>; value: Record<string, unknown> }> = [];
  for (const item of snapshot.docs) {
    const value = generationTaskUpdate(
      item.data() as GenerationTaskSummary,
      activeBookId,
      inactiveBookIds,
    );
    if (value) updates.push({ reference: item.ref, value: { ...value, updated_at: serverTimestamp() } });
  }
  for (let offset = 0; offset < updates.length; offset += 400) {
    const batch = writeBatch(db);
    for (const update of updates.slice(offset, offset + 400)) {
      batch.update(update.reference, update.value);
    }
    await batch.commit();
  }
  recordEstimatedUsage({ reads: snapshot.size, writes: updates.length + 1 });
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

export interface PlannedRequest {
  taskId: string;
  startSegmentId: string;
  priority: number;
  estimatedSeconds: number;
}

export function planGenerationRequests(book: ParsedBook, voiceVersion: string): PlannedRequest[] {
  const requests: PlannedRequest[] = [];
  for (const chapter of book.chapters) {
    let chunkSeconds = 0;
    let chunkStart = "";
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
        });
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
      });
    }
  }
  return requests;
}

export function selectNextFiveHours(
  requests: PlannedRequest[],
  existingTaskIds: ReadonlySet<string>,
): PlannedRequest[] {
  const selected: PlannedRequest[] = [];
  let selectedSeconds = 0;
  for (const request of requests) {
    if (existingTaskIds.has(request.taskId)) continue;
    selected.push(request);
    selectedSeconds += request.estimatedSeconds;
    if (selectedSeconds >= 18_000) break;
  }
  return selected;
}

export async function requestFiveHourGeneration(
  ownerUid: string,
  book: ParsedBook,
  voiceVersion: string,
): Promise<number> {
  if (book.publication_mode !== "PUBLIC_RIGHTS_CONFIRMED") {
    throw new Error("尚未确认这本书的传播权，不能公开生成音频。");
  }
  if (!voiceVersion) throw new Error("请先设置并确认你的声音。");
  const { db } = requireServices();
  const requests = planGenerationRequests(book, voiceVersion);
  const taskCollection = collection(db, `users/${ownerUid}/generationRequests`);
  const existingSnapshot = await getDocs(taskCollection);
  const existingTaskIds = new Set(existingSnapshot.docs.map((item) => item.id));
  const missing = selectNextFiveHours(requests, existingTaskIds);
  recordEstimatedUsage({ reads: existingSnapshot.size });
  let created = 0;
  for (const request of missing) {
    const reference = doc(taskCollection, request.taskId);
    const wasCreated = await runTransaction(db, async (transaction) => {
      const current = await transaction.get(reference);
      if (current.exists()) return false;
      transaction.set(reference, {
        owner_uid: ownerUid,
        task_id: request.taskId,
        book_id: book.book_id,
        status: "QUEUED",
        priority: request.priority,
        attempt_id: 0,
        deletion_generation: 0,
        start_segment_id: request.startSegmentId,
        target_seconds: 600,
        chunk_seconds: 600,
        voice_version: voiceVersion,
        created_at: serverTimestamp(),
        updated_at: serverTimestamp(),
      });
      return true;
    });
    if (wasCreated) created += 1;
  }
  recordEstimatedUsage({ reads: missing.length, writes: created });
  return created;
}
