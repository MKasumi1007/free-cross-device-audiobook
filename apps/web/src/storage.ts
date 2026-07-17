import type { ParsedBook } from "@audiobook/contracts";

import type { CloudBookSummary } from "./cloud";

import { demoBook } from "./demo";

const DATABASE = "audiobook-library";
const VERSION = 2;
const BOOKS = "books";
const PROGRESS = "progress";
const CLOUD_BOOKS = "cloud-books";
const SYNC_MARKERS = "sync-markers";

export interface LocalProgress {
  book_id: string;
  chapter_id: string;
  segment_id: string;
  segment_order?: number;
  audio_offset_seconds?: number;
  cloud_version?: number;
  pending_sync?: boolean;
  updated_at: string;
}

interface CachedCloudBook extends CloudBookSummary {
  cache_id: string;
  owner_uid: string;
  cached_at: string;
}

interface SyncMarker {
  marker_id: string;
  source_sha256: string;
  synced_at: string;
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(BOOKS)) {
        database.createObjectStore(BOOKS, { keyPath: "book_id" });
      }
      if (!database.objectStoreNames.contains(PROGRESS)) {
        database.createObjectStore(PROGRESS, { keyPath: "book_id" });
      }
      if (!database.objectStoreNames.contains(CLOUD_BOOKS)) {
        database.createObjectStore(CLOUD_BOOKS, { keyPath: "cache_id" });
      }
      if (!database.objectStoreNames.contains(SYNC_MARKERS)) {
        database.createObjectStore(SYNC_MARKERS, { keyPath: "marker_id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error || new Error("本地数据库事务已取消。"));
  });
}

export async function loadBooks(): Promise<ParsedBook[]> {
  const database = await openDatabase();
  const transaction = database.transaction(BOOKS, "readonly");
  const completed = transactionDone(transaction);
  const books = await requestResult(transaction.objectStore(BOOKS).getAll() as IDBRequest<ParsedBook[]>);
  await completed;
  database.close();
  if (books.length > 0) {
    return books;
  }
  await saveBook(demoBook);
  return [demoBook];
}

export async function saveBook(book: ParsedBook): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction(BOOKS, "readwrite");
  const completed = transactionDone(transaction);
  await requestResult(transaction.objectStore(BOOKS).put(book));
  await completed;
  database.close();
}

export async function saveProgress(progress: LocalProgress): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction(PROGRESS, "readwrite");
  const completed = transactionDone(transaction);
  await requestResult(transaction.objectStore(PROGRESS).put(progress));
  await completed;
  database.close();
}

export async function loadProgress(bookId: string): Promise<LocalProgress | undefined> {
  const database = await openDatabase();
  const transaction = database.transaction(PROGRESS, "readonly");
  const completed = transactionDone(transaction);
  const value = await requestResult(
    transaction.objectStore(PROGRESS).get(bookId) as IDBRequest<LocalProgress | undefined>,
  );
  await completed;
  database.close();
  return value;
}

export async function loadPendingProgress(): Promise<LocalProgress[]> {
  const database = await openDatabase();
  const transaction = database.transaction(PROGRESS, "readonly");
  const completed = transactionDone(transaction);
  const values = await requestResult(
    transaction.objectStore(PROGRESS).getAll() as IDBRequest<LocalProgress[]>,
  );
  await completed;
  database.close();
  return values.filter((progress) => progress.pending_sync);
}

export async function cacheCloudBooks(ownerUid: string, books: CloudBookSummary[]): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction(CLOUD_BOOKS, "readwrite");
  const completed = transactionDone(transaction);
  const store = transaction.objectStore(CLOUD_BOOKS);
  const current = await requestResult(store.getAll() as IDBRequest<CachedCloudBook[]>);
  await Promise.all(current
    .filter((book) => book.owner_uid === ownerUid)
    .map((book) => requestResult(store.delete(book.cache_id))));
  const cachedAt = new Date().toISOString();
  await Promise.all(books.map((book) => requestResult(store.put({
    ...book,
    cache_id: `${ownerUid}:${book.book_id}`,
    owner_uid: ownerUid,
    cached_at: cachedAt,
  }))));
  await completed;
  database.close();
}

export async function loadCachedCloudBooks(ownerUid: string): Promise<CloudBookSummary[]> {
  const database = await openDatabase();
  const transaction = database.transaction(CLOUD_BOOKS, "readonly");
  const completed = transactionDone(transaction);
  const cached = await requestResult(
    transaction.objectStore(CLOUD_BOOKS).getAll() as IDBRequest<CachedCloudBook[]>,
  );
  await completed;
  database.close();
  return cached.filter((book) => book.owner_uid === ownerUid).map(({ cache_id, owner_uid, cached_at, ...book }) => book);
}

function markerId(ownerUid: string, bookId: string): string {
  return `${ownerUid}:${bookId}`;
}

export async function bookMetadataNeedsSync(ownerUid: string, book: ParsedBook): Promise<boolean> {
  const database = await openDatabase();
  const transaction = database.transaction(SYNC_MARKERS, "readonly");
  const completed = transactionDone(transaction);
  const marker = await requestResult(
    transaction.objectStore(SYNC_MARKERS).get(markerId(ownerUid, book.book_id)) as IDBRequest<SyncMarker | undefined>,
  );
  await completed;
  database.close();
  return marker?.source_sha256 !== book.source_sha256;
}

export async function markBookMetadataSynced(ownerUid: string, book: ParsedBook): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction(SYNC_MARKERS, "readwrite");
  const completed = transactionDone(transaction);
  await requestResult(transaction.objectStore(SYNC_MARKERS).put({
    marker_id: markerId(ownerUid, book.book_id),
    source_sha256: book.source_sha256,
    synced_at: new Date().toISOString(),
  } satisfies SyncMarker));
  await completed;
  database.close();
}
