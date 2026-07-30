import type { ParsedBook } from "@audiobook/contracts";
import { useEffect, useState } from "react";

import {
  buildGenerationQueue,
  enqueueGenerationChapters,
  loadAudioInventory,
  reorderGenerationQueue,
  updateGenerationQueueItem,
  type AudioInventoryItem,
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

function formatRemaining(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "正在计算";
  if (seconds <= 20) return "即将完成";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.max(1, Math.ceil((seconds % 3600) / 60));
  return hours ? `约 ${hours} 小时 ${minutes} 分` : `约 ${minutes} 分钟`;
}

function formatPlaybackDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return minutes ? `${minutes} 分 ${remainder} 秒` : `${remainder} 秒`;
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

function retentionLabel(completedAt: unknown): string {
  const completed = timestampMillis(completedAt);
  if (completed == null) return "完成后保留 5 天";
  const remaining = completed + 5 * 24 * 60 * 60 * 1000 - Date.now();
  if (remaining <= 0) return "等待自动删除";
  const hours = Math.ceil(remaining / (60 * 60 * 1000));
  const days = Math.floor(hours / 24);
  return days ? `${days} 天 ${hours % 24} 小时后删除` : `${hours} 小时后删除`;
}

function stageLabel(stage: string, macOnline: boolean): string {
  if (!macOnline) return "等待 Mac 开机";
  const labels: Record<string, string> = {
    LEASED: "正在准备",
    PREPARING: "正在准备",
    MODEL_LOADING: "正在加载你的声音模型",
    GENERATING: "正在朗读并保存小段",
    ENCODING: "朗读完成，正在合并音频",
    UPLOADING: "正在安全保存到网页",
    READY: "这一段已经可以听",
    FAILED_RETRYABLE: "暂时中断，稍后从断点继续",
  };
  return labels[stage] || "正在准备";
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
  const [audioInventory, setAudioInventory] = useState<AudioInventoryItem[]>([]);
  const [inventoryError, setInventoryError] = useState("");
  const queue = buildGenerationQueue(tasks, books);
  const activeItems = queue.filter((item) => (
    item.status !== "COMPLETED" && item.status !== "REMOVED"
  ));
  const completedItems = queue.filter((item) => item.status === "COMPLETED");
  const generatingItem = activeItems.find((item) => item.status === "GENERATING");
  const waitingItems = activeItems.filter((item) => item.queue_id !== generatingItem?.queue_id);
  const historicalPausedItems = activeItems.filter((item) => item.historical_pause);
  const readyAudio = audioInventory
    .filter((item) => item.status === "READY")
    .sort((left, right) => (
      (timestampMillis(right.completed_at) || 0) - (timestampMillis(left.completed_at) || 0)
    ));
  const selectedBook = books.find((book) => book.book_id === selectedBookId) || books[0];
  const selectedCount = selectedChapters.size;
  const pendingSeconds = activeItems.reduce((total, item) => total + item.estimated_seconds, 0);

  useEffect(() => {
    let active = true;
    async function refreshInventory() {
      try {
        const next = await loadAudioInventory(ownerUid, books);
        if (active) {
          setAudioInventory(next);
          setInventoryError("");
        }
      } catch (error) {
        if (active) {
          setInventoryError(error instanceof Error ? error.message : "暂时无法读取已生成音频。");
        }
      }
    }
    void refreshInventory();
    const timer = window.setInterval(() => void refreshInventory(), 60_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [ownerUid, books]);

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

  async function removeHistoricalPauses() {
    const taskIds = [...new Set(historicalPausedItems.flatMap((item) => item.task_ids))];
    if (!taskIds.length) return;
    setBusy(true);
    try {
      const changed = await updateGenerationQueueItem(ownerUid, taskIds, "REMOVE");
      onNotice(`已整理 ${changed} 个旧暂停任务。需要时重新勾选章节即可生成。`);
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "旧暂停任务没有整理成功。");
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
            {historicalPausedItems.length > 0 && (
              <div className="historical-pause-notice">
                <span>
                  发现 {historicalPausedItems.length} 章是旧版本误留下的暂停记录，不代表现在全部暂停。
                </span>
                <button disabled={busy} onClick={() => void removeHistoricalPauses()}>
                  整理旧暂停
                </button>
              </div>
            )}
            {generatingItem && (
              <article className="generation-now" aria-live="polite">
                <div className="generation-now-topline">
                  <span><i className={macOnline ? "is-online" : ""} />此刻正在处理</span>
                  <strong>{Math.round(generatingItem.progress_percent)}%</strong>
                </div>
                <small>{generatingItem.book_title}</small>
                <h4>{generatingItem.chapter_title}</h4>
                <div
                  className="generation-progress-track"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(generatingItem.progress_percent)}
                >
                  <span style={{ width: `${generatingItem.progress_percent}%` }} />
                </div>
                <p>{stageLabel(generatingItem.progress_stage, macOnline)}</p>
                <dl>
                  <div>
                    <dt>当前小段</dt>
                    <dd>
                      {generatingItem.current_piece_total
                        ? `${generatingItem.current_piece} / ${generatingItem.current_piece_total}`
                        : "准备中"}
                    </dd>
                  </div>
                  <div>
                    <dt>本段还需</dt>
                    <dd>{formatRemaining(generatingItem.eta_seconds)}</dd>
                  </div>
                  <div>
                    <dt>本章还需</dt>
                    <dd>{formatRemaining(generatingItem.chapter_eta_seconds)}</dd>
                  </div>
                  <div>
                    <dt>已生成声音</dt>
                    <dd>{formatPlaybackDuration(generatingItem.generated_audio_seconds)}</dd>
                  </div>
                </dl>
                <small className="generation-now-note">
                  每个小段约 40 字，完成一个就保存一个；关机后会从最近的小段继续。
                </small>
              </article>
            )}
            <div className="queue-subheading">
              <b>接下来生成</b>
              <span>{waitingItems.length} 章</span>
            </div>
            <div className="generation-items" aria-live="polite">
              {!waitingItems.length && !generatingItem && (
                <p className="queue-empty">待生成列表是空的。请从左边选择章节。</p>
              )}
              {!waitingItems.length && generatingItem && (
                <p className="queue-empty queue-empty--compact">后面暂时没有其他章节。</p>
              )}
              {waitingItems.map((item, index) => {
                const action = actionLabel(item);
                const movableItems = waitingItems.filter((entry) => entry.status !== "GENERATING");
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
            {(readyAudio.length > 0 || completedItems.length > 0) && (
              <details className="completed-generation-items">
                <summary>已经可以听 · {readyAudio.length} 段音频</summary>
                {readyAudio.map((item) => (
                  <div key={`${item.book_id}:${item.chunk_id}`}>
                    <span><b>{item.chapter_title}</b><small>{item.book_title}</small></span>
                    <em>
                      {formatPlaybackDuration(Number(item.duration_seconds || 0))}
                      <small>{retentionLabel(item.completed_at)}</small>
                    </em>
                  </div>
                ))}
                <button onClick={onOpenAudioManager}>打开音频空间管理或重新生成</button>
              </details>
            )}
            {inventoryError && <p className="queue-inventory-error">{inventoryError}</p>}
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
