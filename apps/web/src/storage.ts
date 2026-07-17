import type { ParsedBook } from "@audiobook/contracts";

import { demoBook } from "./demo";

const DATABASE = "audiobook-library";
const VERSION = 1;
const BOOKS = "books";
const PROGRESS = "progress";

export interface LocalProgress {
  book_id: string;
  chapter_id: string;
  segment_id: string;
  updated_at: string;
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

export async function loadBooks(): Promise<ParsedBook[]> {
  const database = await openDatabase();
  const transaction = database.transaction(BOOKS, "readonly");
  const books = await requestResult(transaction.objectStore(BOOKS).getAll() as IDBRequest<ParsedBook[]>);
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
  await requestResult(transaction.objectStore(BOOKS).put(book));
  database.close();
}

export async function saveProgress(progress: LocalProgress): Promise<void> {
  const database = await openDatabase();
  const transaction = database.transaction(PROGRESS, "readwrite");
  await requestResult(transaction.objectStore(PROGRESS).put(progress));
  database.close();
}

export async function loadProgress(bookId: string): Promise<LocalProgress | undefined> {
  const database = await openDatabase();
  const transaction = database.transaction(PROGRESS, "readonly");
  const value = await requestResult(
    transaction.objectStore(PROGRESS).get(bookId) as IDBRequest<LocalProgress | undefined>,
  );
  database.close();
  return value;
}
