import type { ParsedBook } from "@audiobook/contracts";
import { useEffect, useState } from "react";

import {
  audioChunkCanBeDeleted,
  calculateAudioStats,
  loadAudioInventory,
  requestAudioDeletion,
  requestAudioRegeneration,
  type AudioChunk,
  type AudioInventoryItem,
  type CloudBookSummary,
} from "./cloud";
import { classifyFirebaseError } from "./firebase-errors";
import { getUsageEstimate } from "./usage";

interface AudioManagerProps {
  ownerUid: string;
  books: ParsedBook[];
  cloudBooks: CloudBookSummary[];
  initialChunks?: AudioChunk[];
  onClose: () => void;
  onNotice: (message: string) => void;
}

interface DeleteSelection {
  label: string;
  chunks: AudioInventoryItem[];
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(0, bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours} 小时 ${minutes} 分` : `${minutes} 分钟`;
}

function chapterTitle(books: ParsedBook[], chunk: AudioChunk): string {
  return books
    .find((book) => book.book_id === chunk.book_id)
    ?.chapters.find((chapter) => chapter.chapter_id === chunk.chapter_id)
    ?.title || "未命名章节";
}

function testInventory(books: ParsedBook[], chunks: AudioChunk[]): AudioInventoryItem[] {
  return chunks.map((chunk) => ({
    ...chunk,
    book_title: books.find((book) => book.book_id === chunk.book_id)?.title || "测试书籍",
    chapter_title: chapterTitle(books, chunk),
  }));
}

export function AudioManager({
  ownerUid,
  books,
  cloudBooks,
  initialChunks,
  onClose,
  onNotice,
}: AudioManagerProps) {
  const [items, setItems] = useState<AudioInventoryItem[]>(() => (
    initialChunks ? testInventory(books, initialChunks) : []
  ));
  const [selectedBookId, setSelectedBookId] = useState("ALL");
  const [selection, setSelection] = useState<DeleteSelection | null>(null);
  const [busy, setBusy] = useState(!initialChunks);
  const usage = getUsageEstimate();

  async function refresh() {
    if (initialChunks) return;
    setBusy(true);
    try {
      const loaded = await loadAudioInventory(ownerUid, cloudBooks);
      setItems(loaded.map((chunk) => ({
        ...chunk,
        chapter_title: chapterTitle(books, chunk),
      })));
    } catch (error) {
      onNotice(classifyFirebaseError(error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
    if (initialChunks) return;
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(timer);
  }, []);

  const visible = selectedBookId === "ALL"
    ? items
    : items.filter((item) => item.book_id === selectedBookId);
  const total = calculateAudioStats(items);
  const visibleStats = calculateAudioStats(visible);
  const bookIds = [...new Set(items.map((item) => item.book_id))];
  const chapters = [...new Set(visible.map((item) => item.chapter_id))];

  function askToDelete(label: string, chunks: AudioInventoryItem[]) {
    const deletable = chunks.filter(audioChunkCanBeDeleted);
    if (!deletable.length) {
      onNotice("这里没有可以删除的远程音频。");
      return;
    }
    setSelection({ label, chunks: deletable });
  }

  async function confirmDelete() {
    if (!selection) return;
    setBusy(true);
    try {
      if (initialChunks) {
        const ids = new Set(selection.chunks.map((chunk) => chunk.chunk_id));
        setItems((current) => current.map((chunk) => (
          ids.has(chunk.chunk_id) ? { ...chunk, status: "DELETING" } : chunk
        )));
        onNotice("删除请求已保存；Mac 在线后会核对并完成删除。");
      } else {
        const count = await requestAudioDeletion(ownerUid, selection.chunks);
        onNotice(count
          ? `已安全安排 ${count} 段音频删除，书籍、正文、书签和进度都会保留。`
          : "这些音频已经在删除中或已删除，不会重复操作。");
        await refresh();
      }
      setSelection(null);
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "删除请求没有保存成功。");
    } finally {
      setBusy(false);
    }
  }

  async function regenerate(chunk: AudioInventoryItem) {
    setBusy(true);
    try {
      if (initialChunks) {
        setItems((current) => current.map((item) => (
          item.chunk_id === chunk.chunk_id ? { ...item, status: "FAILED_RETRYABLE" } : item
        )));
      } else {
        await requestAudioRegeneration(ownerUid, chunk);
        await refresh();
      }
      onNotice("已从这段原来的文字位置安排重新生成。");
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "重新生成请求没有保存成功。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop audio-manager-backdrop" role="presentation">
      <section className="audio-manager" role="dialog" aria-modal="true" aria-labelledby="audio-manager-title">
        <header className="audio-manager-heading">
          <div>
            <span className="modal-kicker">远程音频管理</span>
            <h2 id="audio-manager-title">音频空间</h2>
            <p>统一管理公开音频和账号私有音频。音频生成满 5 天后会在 Mac 下次在线时自动删除，但书、正文、书签、阅读进度和声音样本都会保留。</p>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="关闭音频空间">×</button>
        </header>

        <div className="audio-summary-grid">
          <article><span>正在占用</span><b>{formatBytes(total.bytes)}</b><small>{total.chunks} 段音频</small></article>
          <article><span>可听时长</span><b>{formatDuration(total.duration_seconds)}</b><small>{total.deleting ? `${total.deleting} 段删除中` : "远端缓存"}</small></article>
          <article><span>今日同步估算</span><b>{usage.reads.toLocaleString()}</b><small>读取 · 免费保护开启</small></article>
        </div>

        <div className="audio-manager-toolbar">
          <label>
            <span>查看范围</span>
            <select value={selectedBookId} onChange={(event) => setSelectedBookId(event.target.value)}>
              <option value="ALL">全部书籍</option>
              {bookIds.map((bookId) => (
                <option value={bookId} key={bookId}>
                  {items.find((item) => item.book_id === bookId)?.book_title || bookId}
                </option>
              ))}
            </select>
          </label>
          <div>
            <span>{visibleStats.chunks} 段 · {formatBytes(visibleStats.bytes)}</span>
            <button
              className="danger-button"
              onClick={() => askToDelete(selectedBookId === "ALL" ? "全部书籍" : "这本书", visible)}
              disabled={busy || !visible.some(audioChunkCanBeDeleted)}
            >
              删除这个范围的音频
            </button>
          </div>
        </div>

        <div className="audio-inventory" aria-live="polite">
          {busy && !items.length && <p className="audio-empty">正在核对远程音频...</p>}
          {!busy && !items.length && <p className="audio-empty">还没有生成过远程音频。</p>}
          {chapters.map((chapterId) => {
            const chapterChunks = visible.filter((item) => item.chapter_id === chapterId);
            const stats = calculateAudioStats(chapterChunks);
            return (
              <section className="audio-chapter" key={`${chapterChunks[0]?.book_id}-${chapterId}`}>
                <header>
                  <div><small>{chapterChunks[0]?.book_title}</small><b>{chapterChunks[0]?.chapter_title}</b></div>
                  <span>{stats.chunks} 段 · {formatBytes(stats.bytes)}</span>
                  <button
                    className="text-danger-button"
                    disabled={busy || !chapterChunks.some(audioChunkCanBeDeleted)}
                    onClick={() => askToDelete(`《${chapterChunks[0]?.book_title}》的“${chapterChunks[0]?.chapter_title}”`, chapterChunks)}
                  >删除本章音频</button>
                </header>
                {chapterChunks.map((chunk, index) => (
                  <div className="audio-chunk-row" key={chunk.chunk_id}>
                    <span className={`audio-status audio-status--${chunk.status.toLowerCase()}`}>
                      {chunk.status === "READY" && "可播放"}
                      {chunk.status === "FAILED_RETRYABLE" && "等待生成"}
                      {chunk.status === "DELETING" && "删除中"}
                      {chunk.status === "DELETED" && "已删除"}
                    </span>
                    <b>第 {index + 1} 段 · {chunk.storage_mode === "PRIVATE_FIRESTORE" ? "私有" : "公开"}</b>
                    <span>{formatDuration(chunk.duration_seconds)} · {formatBytes(chunk.byte_size)}</span>
                    {audioChunkCanBeDeleted(chunk) && (
                      <button className="text-danger-button" disabled={busy} onClick={() => askToDelete("这一段", [chunk])}>删除</button>
                    )}
                    {chunk.status === "DELETED" && (
                      <button className="text-action-button" disabled={busy} onClick={() => void regenerate(chunk)}>从原位置重新生成</button>
                    )}
                  </div>
                ))}
              </section>
            );
          })}
        </div>

        <footer className="audio-manager-footer">
          <span>可以提前手动删除；到期删除后仍会保留起始文字位置和校验信息，便于以后安全重建。</span>
          <button className="quiet-button" onClick={onClose}>关闭</button>
        </footer>

        {selection && (
          <div className="audio-confirm" role="alertdialog" aria-modal="true" aria-labelledby="audio-confirm-title">
            <section>
              <span className="modal-kicker">最后确认</span>
              <h3 id="audio-confirm-title">删除{selection.label}的远程音频？</h3>
              <p>将永久删除选中的 {selection.chunks.length} 段公开或私有音频（{formatBytes(calculateAudioStats(selection.chunks).bytes)}）。这个操作不能撤销，但书籍、正文、书签、进度和你的声音都会保留。</p>
              <div className="modal-actions">
                <button className="quiet-button" onClick={() => setSelection(null)}>取消</button>
                <button className="danger-button" onClick={() => void confirmDelete()} disabled={busy}>确认删除音频</button>
              </div>
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
