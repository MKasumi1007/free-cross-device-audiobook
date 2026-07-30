import { beforeEach, describe, expect, it } from "vitest";

import { demoBook } from "./demo";
import {
  bookMetadataNeedsSync,
  cacheCloudBooks,
  deleteLocalBook,
  hideBook,
  loadCachedCloudBooks,
  loadHiddenBooks,
  loadPendingProgress,
  loadProgress,
  markBookMetadataSynced,
  saveBook,
  saveProgress,
  unhideBook,
  type LocalProgress,
} from "./storage";

const DATABASE = "audiobook-library";

function deleteDatabase(): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.deleteDatabase(DATABASE);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("测试数据库仍被占用。"));
  });
}

beforeEach(deleteDatabase);

describe("IndexedDB 离线恢复", () => {
  it("保留待同步进度，并在远端版本胜出后清除待办", async () => {
    const offlineProgress: LocalProgress = {
      book_id: demoBook.book_id,
      chapter_id: demoBook.chapters[0].chapter_id,
      segment_id: demoBook.chapters[0].segments[1].segment_id,
      segment_order: 1,
      audio_offset_seconds: 12,
      cloud_version: 1,
      pending_sync: true,
      updated_at: "2026-07-17T00:00:00.000Z",
    };
    await saveProgress(offlineProgress);
    expect(await loadPendingProgress()).toEqual([offlineProgress]);

    const mergedProgress: LocalProgress = {
      ...offlineProgress,
      segment_id: demoBook.chapters[1].segments[0].segment_id,
      chapter_id: demoBook.chapters[1].chapter_id,
      segment_order: 0,
      cloud_version: 2,
      pending_sync: false,
      updated_at: "2026-07-17T00:01:00.000Z",
    };
    await saveProgress(mergedProgress);
    expect(await loadProgress(demoBook.book_id)).toEqual(mergedProgress);
    expect(await loadPendingProgress()).toEqual([]);
  });

  it("按账号隔离缓存的云端书架", async () => {
    const summary = {
      book_id: "book-cloud-a",
      title: "云端测试书",
      author: "测试作者",
      source_format: "EPUB" as const,
      source_sha256: "a".repeat(64),
      publication_mode: "LOCAL_ONLY" as const,
      chapter_count: 2,
      segment_count: 20,
    };
    await cacheCloudBooks("owner-a", [summary]);
    await cacheCloudBooks("owner-b", [{ ...summary, book_id: "book-cloud-b" }]);

    expect((await loadCachedCloudBooks("owner-a")).map((book) => book.book_id)).toEqual(["book-cloud-a"]);
    expect((await loadCachedCloudBooks("owner-b")).map((book) => book.book_id)).toEqual(["book-cloud-b"]);
  });

  it("书籍事务完成后再记录元数据同步标记", async () => {
    await saveBook(demoBook);
    expect(await bookMetadataNeedsSync("owner-a", demoBook)).toBe(true);
    await markBookMetadataSynced("owner-a", demoBook);
    expect(await bookMetadataNeedsSync("owner-a", demoBook)).toBe(false);
    expect(await bookMetadataNeedsSync("owner-b", demoBook)).toBe(true);
  });

  it("隐藏书籍不会删除本机内容，并且可以恢复", async () => {
    await saveBook(demoBook);
    await hideBook({
      book_id: demoBook.book_id,
      title: demoBook.title,
      author: demoBook.author,
    });
    expect(await loadHiddenBooks()).toMatchObject([{
      book_id: demoBook.book_id,
      title: demoBook.title,
    }]);

    await unhideBook(demoBook.book_id);
    expect(await loadHiddenBooks()).toEqual([]);
  });

  it("永久删除会清除正文、进度、缓存、隐藏项和同步标记", async () => {
    await saveBook(demoBook);
    await saveProgress({
      book_id: demoBook.book_id,
      chapter_id: demoBook.chapters[0].chapter_id,
      segment_id: demoBook.chapters[0].segments[0].segment_id,
      updated_at: "2026-07-30T00:00:00.000Z",
    });
    await hideBook({
      book_id: demoBook.book_id,
      title: demoBook.title,
      author: demoBook.author,
    });
    await cacheCloudBooks("owner-a", [{
      book_id: demoBook.book_id,
      title: demoBook.title,
      author: demoBook.author,
      source_format: demoBook.source_format,
      source_sha256: demoBook.source_sha256,
      publication_mode: demoBook.publication_mode,
      chapter_count: demoBook.chapters.length,
      segment_count: 2,
    }]);
    await markBookMetadataSynced("owner-a", demoBook);

    await deleteLocalBook(demoBook.book_id, "owner-a");

    expect(await loadProgress(demoBook.book_id)).toBeUndefined();
    expect(await loadHiddenBooks()).toEqual([]);
    expect(await loadCachedCloudBooks("owner-a")).toEqual([]);
    expect(await bookMetadataNeedsSync("owner-a", demoBook)).toBe(true);
  });
});
