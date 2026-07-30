import type { ParsedBook } from "@audiobook/contracts";
import { useState } from "react";

import {
  buildGenerationQueue,
  enqueueGenerationChapters,
  reorderGenerationQueue,
  updateGenerationQueueItem,
  type ChapterGenerationSelection,
  type GenerationQueueAction,
  type GenerationQueueItem,
  type GenerationTaskSummary,
} from "./cloud";

interface GenerationQueueProps {
  ownerUid: string;
  books: ParsedBook[];
  tasks: GenerationTaskSummary[];
  voiceVersion: string;
  initialBookId?: string;
  initialChapterId?: string;
  macOnline: boolean;
  onClose: () => void;
  onNotice: (message: string) => void;
  onOpenAudioManager: () => void;
}

const STATUS_LABELS: Record<GenerationQueueItem["status"], string> = {
  GENERATING: "正在生成",
  QUEUED: "等待生成",
  PAUSED: "已暂停",
  FAILED: "需要重试",
  COMPLETED: "已经完成",
  REMOVED: "已从队列移除",
};

function selectionKey(bookId: string, chapterId: string): string {
  return `${bookId}:${chapterId}`;
}

function formatDuration(seconds: number): string {
  if (!seconds) return "时长计算中";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.max(1, Math.round((seconds % 3600) / 60));
  return hours ? `约 ${hours} 小时 ${minutes} 分` : `约 ${minutes} 分钟`;
}

function actionLabel(item: GenerationQueueItem): {
  action: GenerationQueueAction;
  label: string;
} {
  if (item.status === "PAUSED" || item.status === "FAILED" || item.status === "REMOVED") {
    return { action: "RESUME", label: item.status === "REMOVED" ? "重新加入" : "继续" };
  }
  return {
    action: "PAUSE",
    label: item.status === "GENERATING" ? "本段后暂停" : "暂停",
  };
}

export function GenerationQueue({
  ownerUid,
  books,
  tasks,
  voiceVersion,
  initialBookId = "",
  initialChapterId = "",
  macOnline,
  onClose,
  onNotice,
  onOpenAudioManager,
}: GenerationQueueProps) {
  const [selectedBookId, setSelectedBookId] = useState(
    initialBookId || books[0]?.book_id || "",
  );
  const [selectedChapters, setSelectedChapters] = useState<Set<string>>(() => (
    initialBookId && initialChapterId
      ? new Set([selectionKey(initialBookId, initialChapterId)])
      : new Set()
  ));
  const [busy, setBusy] = useState(false);
  const queue = buildGenerationQueue(tasks, books);
  const activeItems = queue.filter((item) => (
    item.status !== "COMPLETED" && item.status !== "REMOVED"
  ));
  const completedItems = queue.filter((item) => item.status === "COMPLETED");
  const selectedBook = books.find((book) => book.book_id === selectedBookId) || books[0];
  const selectedCount = selectedChapters.size;
  const pendingSeconds = activeItems.reduce((total, item) => total + item.estimated_seconds, 0);

  function toggleChapter(bookId: string, chapterId: string) {
    const key = selectionKey(bookId, chapterId);
    setSelectedChapters((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleWholeBook() {
    if (!selectedBook) return;
    const keys = selectedBook.chapters.map((chapter) => (
      selectionKey(selectedBook.book_id, chapter.chapter_id)
    ));
    const allSelected = keys.every((key) => selectedChapters.has(key));
    setSelectedChapters((current) => {
      const next = new Set(current);
      keys.forEach((key) => {
        if (allSelected) next.delete(key);
        else next.add(key);
      });
      return next;
    });
  }

  async function addSelectedChapters() {
    if (!voiceVersion) {
      onNotice("请先在 Mac 确认一次你的声音，再安排章节。");
      return;
    }
    const selections: ChapterGenerationSelection[] = books
      .map((book) => ({
        book,
        chapter_ids: book.chapters
          .filter((chapter) => selectedChapters.has(selectionKey(book.book_id, chapter.chapter_id)))
          .map((chapter) => chapter.chapter_id),
      }))
      .filter((selection) => selection.chapter_ids.length > 0);
    if (!selections.length) {
      onNotice("请先勾选至少一个章节。");
      return;
    }
    setBusy(true);
    try {
      const result = await enqueueGenerationChapters(ownerUid, selections, voiceVersion);
      setSelectedChapters(new Set());
      if (result.created || result.resumed) {
        onNotice(
          `已加入 ${result.chapters} 章：新建 ${result.created} 段，恢复 ${result.resumed} 段。`,
        );
      } else {
        onNotice("这些章节已经生成或已在队列中，不会重复生成。");
      }
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "章节没有加入待生成列表。");
    } finally {
      setBusy(false);
    }
  }

  async function runAction(item: GenerationQueueItem, action: GenerationQueueAction) {
    setBusy(true);
    try {
      const changed = await updateGenerationQueueItem(ownerUid, item.task_ids, action);
      const messages: Record<GenerationQueueAction, string> = {
        PAUSE: item.status === "GENERATING"
          ? "正在生成的小段会正常完成，之后暂停这一章。"
          : "这一章已暂停。",
        RESUME: "这一章已重新加入待生成列表。",
        REMOVE: item.status === "GENERATING"
          ? "正在生成的小段会正常完成，之后从待生成列表移除。"
          : "这一章已从待生成列表移除。",
      };
      onNotice(changed ? messages[action] : "这一章的状态已经更新，不需要重复操作。");
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "队列操作没有保存成功。");
    } finally {
      setBusy(false);
    }
  }

  async function moveItem(item: GenerationQueueItem, direction: -1 | 1) {
    const movable = activeItems.filter((entry) => entry.status !== "GENERATING");
    const index = movable.findIndex((entry) => entry.queue_id === item.queue_id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= movable.length) return;
    const reordered = [...movable];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    const generating = activeItems.filter((entry) => entry.status === "GENERATING");
    setBusy(true);
    try {
      await reorderGenerationQueue(ownerUid, [...generating, ...reordered]);
      onNotice(direction < 0 ? "已提前这一章。" : "已把这一章向后移动。");
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "队列顺序没有保存成功。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop generation-queue-backdrop" role="presentation">
      <section
        className="generation-queue"
        role="dialog"
        aria-modal="true"
        aria-labelledby="generation-queue-title"
      >
        <header className="generation-queue-heading">
          <div>
            <span className="modal-kicker">由你决定下一章</span>
            <h2 id="generation-queue-title">待生成列表</h2>
            <p>勾选任意书的任意章节，再排列先后。Mac 只会按这里的顺序生成。</p>
          </div>
          <div className="generation-queue-health">
            <i className={macOnline ? "is-online" : ""} />
            {macOnline ? "Mac 在线" : "Mac 关机也会保留队列"}
          </div>
          <button className="modal-close" onClick={onClose} aria-label="关闭待生成列表">×</button>
        </header>

        <div className="generation-queue-body">
          <section className="chapter-picker">
            <div className="queue-section-title">
              <div><span>第一步</span><h3>选择章节</h3></div>
              {selectedBook && <button onClick={toggleWholeBook}>全选 / 取消本书</button>}
            </div>
            {books.length ? (
              <>
                <label className="queue-book-select">
                  <span>从哪本书选择</span>
                  <select
                    value={selectedBook?.book_id || ""}
                    onChange={(event) => setSelectedBookId(event.target.value)}
                  >
                    {books.map((book) => (
                      <option value={book.book_id} key={book.book_id}>{book.title}</option>
                    ))}
                  </select>
                </label>
                <div className="chapter-choice-list">
                  {selectedBook?.chapters.map((chapter) => {
                    const key = selectionKey(selectedBook.book_id, chapter.chapter_id);
                    const checked = selectedChapters.has(key);
                    const existing = queue.find((item) => item.queue_id === key);
                    return (
                      <label className={checked ? "is-selected" : ""} key={chapter.chapter_id}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleChapter(selectedBook.book_id, chapter.chapter_id)}
                        />
                        <span className="chapter-choice-number">
                          {String(chapter.order + 1).padStart(2, "0")}
                        </span>
                        <span>
                          <b>{chapter.title}</b>
                          <small>
                            {existing
                              ? `${STATUS_LABELS[existing.status]} · ${existing.ready_chunks}/${existing.total_chunks} 段完成`
                              : `${chapter.segments.length} 段文字`}
                          </small>
                        </span>
                      </label>
                    );
                  })}
                </div>
              </>
            ) : (
              <p className="queue-empty">
                这台设备还没有载入书籍正文。请先从书架打开一本书，再回来选择章节。
              </p>
            )}
            <div className="chapter-picker-footer">
              <span>已选 {selectedCount} 章</span>
              <button
                className="primary-button"
                disabled={busy || !selectedCount || !voiceVersion}
                onClick={() => void addSelectedChapters()}
              >
                加入待生成列表
              </button>
            </div>
            {!voiceVersion && (
              <p className="queue-voice-warning">还没有找到已确认的声音，请先在 Mac 设置“我的声音”。</p>
            )}
          </section>

          <section className="queue-column">
            <div className="queue-section-title">
              <div><span>第二步</span><h3>排列生成顺序</h3></div>
              <small>{activeItems.length} 章 · {formatDuration(pendingSeconds)}</small>
            </div>
            <p className="queue-explanation">
              约保留 5 小时可听音频；旧音频满 5 天删除后，Mac 会继续生成列表中的下一章。
            </p>
            <div className="generation-items" aria-live="polite">
              {!activeItems.length && (
                <p className="queue-empty">待生成列表是空的。请从左边选择章节。</p>
              )}
              {activeItems.map((item, index) => {
                const action = actionLabel(item);
                const movableItems = activeItems.filter((entry) => entry.status !== "GENERATING");
                const movableIndex = movableItems.findIndex((entry) => entry.queue_id === item.queue_id);
                return (
                  <article className={`generation-item generation-item--${item.status.toLowerCase()}`} key={item.queue_id}>
                    <div className="generation-item-rank">
                      {item.status === "GENERATING" ? <span className="queue-pulse" /> : index + 1}
                    </div>
                    <div className="generation-item-copy">
                      <small>{item.book_title}</small>
                      <b>{item.chapter_title}</b>
                      <span>
                        {STATUS_LABELS[item.status]} · {item.ready_chunks}/{item.total_chunks} 段完成
                        {item.estimated_seconds ? ` · ${formatDuration(item.estimated_seconds)}` : ""}
                      </span>
                    </div>
                    <div className="generation-item-order">
                      <button
                        disabled={busy || item.status === "GENERATING" || movableIndex <= 0}
                        onClick={() => void moveItem(item, -1)}
                        aria-label={`提前 ${item.chapter_title}`}
                      >↑</button>
                      <button
                        disabled={busy || item.status === "GENERATING" || movableIndex >= movableItems.length - 1}
                        onClick={() => void moveItem(item, 1)}
                        aria-label={`延后 ${item.chapter_title}`}
                      >↓</button>
                    </div>
                    <div className="generation-item-actions">
                      <button disabled={busy} onClick={() => void runAction(item, action.action)}>
                        {action.label}
                      </button>
                      <button
                        className="is-danger"
                        disabled={busy}
                        onClick={() => void runAction(item, "REMOVE")}
                      >
                        {item.status === "GENERATING" ? "本段后移除" : "移除"}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
            {completedItems.length > 0 && (
              <details className="completed-generation-items">
                <summary>已完成 {completedItems.length} 章</summary>
                {completedItems.map((item) => (
                  <div key={item.queue_id}>
                    <span><b>{item.chapter_title}</b><small>{item.book_title}</small></span>
                    <em>{item.total_chunks} 段可听</em>
                  </div>
                ))}
                <button onClick={onOpenAudioManager}>打开音频空间管理或重新生成</button>
              </details>
            )}
          </section>
        </div>

        <footer className="generation-queue-footer">
          <span>正在生成的小段不会被强行打断；你的调整从下一段开始生效。</span>
          <button className="quiet-button" onClick={onClose}>完成</button>
        </footer>
      </section>
    </div>
  );
}
