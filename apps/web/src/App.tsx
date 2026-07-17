import type { Chapter, ParsedBook, TextSegment } from "@audiobook/contracts";
import { useEffect, useState } from "react";

import { chooseBookOnMac } from "./agent";
import { canAddBooks, currentPlatformSignals } from "./platform";
import { loadBooks, loadProgress, saveBook, saveProgress } from "./storage";

function chapterPreview(chapter: Chapter): string {
  return chapter.segments.find((segment) => segment.spoken_text)?.display_text.slice(0, 54) || "";
}

function segmentClass(segment: TextSegment, selected: boolean): string {
  const classes = ["reader-segment", `reader-segment--${segment.kind.toLowerCase()}`];
  if (selected) classes.push("reader-segment--selected");
  return classes.join(" ");
}

export function App() {
  const [books, setBooks] = useState<ParsedBook[]>([]);
  const [selectedBookId, setSelectedBookId] = useState("");
  const [selectedChapterId, setSelectedChapterId] = useState("");
  const [selectedSegmentId, setSelectedSegmentId] = useState("");
  const [canAdd, setCanAdd] = useState(() => canAddBooks(currentPlatformSignals()));
  const [showImport, setShowImport] = useState(false);
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [busy, setBusy] = useState(true);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    loadBooks()
      .then((items) => {
        if (!active) return;
        setBooks(items);
        setSelectedBookId((current) => current || items[0]?.book_id || "");
      })
      .catch(() => setNotice("本地书架暂时打不开，请刷新页面重试。"))
      .finally(() => active && setBusy(false));
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const onResize = () => setCanAdd(canAddBooks(currentPlatformSignals()));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const selectedBook = books.find((book) => book.book_id === selectedBookId);
  const selectedChapter = selectedBook?.chapters.find((chapter) => chapter.chapter_id === selectedChapterId)
    ?? selectedBook?.chapters[0];

  useEffect(() => {
    if (!selectedBook) return;
    let active = true;
    loadProgress(selectedBook.book_id).then((progress) => {
      if (!active) return;
      const chapterId = progress?.chapter_id || selectedBook.chapters[0]?.chapter_id || "";
      setSelectedChapterId(chapterId);
      setSelectedSegmentId(progress?.segment_id || "");
    });
    return () => {
      active = false;
    };
  }, [selectedBook]);

  async function importBook() {
    setBusy(true);
    setNotice("正在等待你在 Mac 文件选择器中选书...");
    try {
      const book = await chooseBookOnMac(rightsConfirmed);
      if (!book) {
        setNotice("没有选择文件，书架没有变化。");
        return;
      }
      await saveBook(book);
      setBooks((current) => [book, ...current.filter((item) => item.book_id !== book.book_id)]);
      setSelectedBookId(book.book_id);
      setShowImport(false);
      setRightsConfirmed(false);
      setNotice(`《${book.title}》已加入书架。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "添加书籍失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  function openChapter(chapter: Chapter) {
    setSelectedChapterId(chapter.chapter_id);
    setSelectedSegmentId(chapter.segments[0]?.segment_id || "");
  }

  function markPosition(segment: TextSegment) {
    if (!selectedBook || !selectedChapter) return;
    setSelectedSegmentId(segment.segment_id);
    void saveProgress({
      book_id: selectedBook.book_id,
      chapter_id: selectedChapter.chapter_id,
      segment_id: segment.segment_id,
      updated_at: new Date().toISOString(),
    });
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => setSelectedBookId("")} aria-label="返回书架">
          <span className="brand-seal">听</span>
          <span><b>听见书页</b><small>边听，边读，记住每一处停留</small></span>
        </button>
        <div className="topbar-actions">
          <span className="sync-state"><i /> 本机书架</span>
          {canAdd && (
            <button className="add-book-button" onClick={() => setShowImport(true)}>
              <span>+</span> 添加书籍
            </button>
          )}
        </div>
      </header>

      {notice && <div className="notice" role="status">{notice}</div>}

      {!selectedBook ? (
        <main className="shelf-page">
          <section className="shelf-intro">
            <p className="eyebrow">我的书架</p>
            <h1>今天，想听哪一本？</h1>
            <p>书和进度先安全留在这台设备。云端同步会在下一阶段接上。</p>
          </section>
          {!canAdd && <p className="mobile-add-hint">请在 Mac 上添加新书</p>}
          <section className="book-grid" aria-label="书架">
            {books.map((book, index) => (
              <button
                className={`book-card book-card--tone-${index % 4}`}
                key={book.book_id}
                onClick={() => setSelectedBookId(book.book_id)}
              >
                <span className="book-spine" />
                <span className="book-format">{book.source_format}</span>
                <span className="book-title">{book.title}</span>
                <span className="book-author">{book.author || "作者未注明"}</span>
                <span className="book-meta">{book.chapters.length} 章 · {book.publication_mode === "LOCAL_ONLY" ? "仅本机" : "已确认传播权"}</span>
              </button>
            ))}
          </section>
          {busy && <p className="loading">正在整理书架...</p>}
        </main>
      ) : (
        <main className="reader-layout">
          <aside className="toc-panel">
            <button className="back-link" onClick={() => setSelectedBookId("")}>← 返回书架</button>
            <p className="eyebrow">正在阅读</p>
            <h1>{selectedBook.title}</h1>
            <p className="book-byline">{selectedBook.author || "作者未注明"}</p>
            <div className="rights-badge">
              {selectedBook.publication_mode === "LOCAL_ONLY" ? "仅在本机可用" : "已确认可公开"}
            </div>
            <nav className="toc-list" aria-label="目录">
              {selectedBook.chapters.map((chapter) => (
                <button
                  key={chapter.chapter_id}
                  className={chapter.chapter_id === selectedChapter?.chapter_id ? "is-active" : ""}
                  onClick={() => openChapter(chapter)}
                >
                  <span>{String(chapter.order + 1).padStart(2, "0")}</span>
                  <b>{chapter.title}</b>
                  <small>{chapterPreview(chapter)}</small>
                </button>
              ))}
            </nav>
          </aside>

          <article className="reading-page">
            <div className="reading-heading">
              <span>第 {selectedChapter ? selectedChapter.order + 1 : 1} 章</span>
              <h2>{selectedChapter?.title}</h2>
              <p>点击一段文字，会记住你读到的位置。</p>
            </div>
            <div className="reading-text">
              {selectedChapter?.segments.map((segment) => (
                <button
                  key={segment.segment_id}
                  className={segmentClass(segment, selectedSegmentId === segment.segment_id)}
                  onClick={() => markPosition(segment)}
                >
                  {segment.display_text}
                </button>
              ))}
            </div>
          </article>
        </main>
      )}

      {selectedBook && (
        <footer className="player-dock">
          <button disabled aria-label="上一段">‹</button>
          <button className="play-button" disabled aria-label="播放">▶</button>
          <button disabled aria-label="下一段">›</button>
          <div className="player-status">
            <b>正文已准备</b>
            <span>音频将在 Mac 语音生成阶段接入</span>
          </div>
          <div className="player-progress"><i /></div>
          <span className="player-time">00:00 / --:--</span>
        </footer>
      )}

      {showImport && (
        <div className="modal-backdrop" role="presentation">
          <section className="import-modal" role="dialog" aria-modal="true" aria-labelledby="import-title">
            <span className="modal-kicker">从这台 Mac 添加</span>
            <h2 id="import-title">选择一本 EPUB 或 TXT</h2>
            <p>点击继续后会出现 Mac 文件选择器。工具不会扫描你的桌面，也不会读取未选择的文件。</p>
            <label className="rights-check">
              <input
                type="checkbox"
                checked={rightsConfirmed}
                onChange={(event) => setRightsConfirmed(event.target.checked)}
              />
              <span><b>我确认有权公开传播这本书</b><small>不勾选时只保存在本机，不会创建公开上传任务。</small></span>
            </label>
            <div className="modal-actions">
              <button className="quiet-button" onClick={() => setShowImport(false)}>取消</button>
              <button className="primary-button" onClick={() => void importBook()} disabled={busy}>打开文件选择器</button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
