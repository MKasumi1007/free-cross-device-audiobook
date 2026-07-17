import type { Chapter, ParsedBook, TextSegment } from "@audiobook/contracts";
import type { User } from "firebase/auth";
import { useEffect, useRef, useState } from "react";

import {
  chooseBookOnMac,
  chooseVoiceOnMac,
  confirmVoice,
  getVoiceStatus,
  startPairingOnMac,
  startVoicePreview,
  voicePreviewUrl,
  type VoiceStatus,
} from "./agent";
import { signInWithGoogle, signOutCurrentUser, watchAuth } from "./auth";
import {
  loadCloudProgress,
  requestFiveHourGeneration,
  saveProgressOptimistically,
  syncBookMetadata,
  watchCloudBooks,
  type CloudBookSummary,
  type ProgressInput,
} from "./cloud";
import { registerCurrentDevice } from "./device";
import { classifyFirebaseError } from "./firebase-errors";
import { firebaseIsConfigured } from "./firebase";
import {
  pairMacAgent,
  revokeMacAgent,
  watchMacAgents,
  type WorkerLink,
} from "./pairing";
import { canAddBooks, currentPlatformSignals } from "./platform";
import {
  bookMetadataNeedsSync,
  cacheCloudBooks,
  loadBooks,
  loadCachedCloudBooks,
  loadPendingProgress,
  loadProgress,
  markBookMetadataSynced,
  saveBook,
  saveProgress,
  type LocalProgress,
} from "./storage";

const DEMO_BOOK_ID = "356fc83a-1b37-5571-bb94-9d168a6a7c2f";

function chapterPreview(chapter: Chapter): string {
  return chapter.segments.find((segment) => segment.spoken_text)?.display_text.slice(0, 54) || "";
}

function segmentClass(segment: TextSegment, selected: boolean): string {
  const classes = ["reader-segment", `reader-segment--${segment.kind.toLowerCase()}`];
  if (selected) classes.push("reader-segment--selected");
  return classes.join(" ");
}

function cloudSummaryFor(book: ParsedBook, cloudBooks: CloudBookSummary[]): CloudBookSummary | undefined {
  return cloudBooks.find((cloudBook) => cloudBook.book_id === book.book_id);
}

export function App() {
  const [books, setBooks] = useState<ParsedBook[]>([]);
  const [cloudBooks, setCloudBooks] = useState<CloudBookSummary[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [selectedBookId, setSelectedBookId] = useState("");
  const [selectedChapterId, setSelectedChapterId] = useState("");
  const [selectedSegmentId, setSelectedSegmentId] = useState("");
  const [canAdd, setCanAdd] = useState(() => canAddBooks(currentPlatformSignals()));
  const [showImport, setShowImport] = useState(false);
  const [showPairing, setShowPairing] = useState(false);
  const [showDisconnectMac, setShowDisconnectMac] = useState(false);
  const [showVoice, setShowVoice] = useState(false);
  const [voiceTranscript, setVoiceTranscript] = useState("");
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null);
  const [pairingCode, setPairingCode] = useState("");
  const [workerLinks, setWorkerLinks] = useState<WorkerLink[]>([]);
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [busy, setBusy] = useState(true);
  const [notice, setNotice] = useState("");
  const progressQueues = useRef(new Map<string, Promise<void>>());
  const firebaseConfigured = firebaseIsConfigured();

  useEffect(() => watchAuth(setUser), []);

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

  useEffect(() => {
    if (!user) {
      setCloudBooks([]);
      return;
    }
    let active = true;
    let unsubscribe: () => void = () => {};
    void loadCachedCloudBooks(user.uid).then((cached) => active && setCloudBooks(cached));
    void registerCurrentDevice(user.uid).catch((error) => setNotice(classifyFirebaseError(error).message));
    void (async () => {
      for (const book of books) {
        if (book.book_id === DEMO_BOOK_ID || !(await bookMetadataNeedsSync(user.uid, book))) continue;
        await syncBookMetadata(user.uid, book);
        await markBookMetadataSynced(user.uid, book);
      }
    })().catch((error) => setNotice(classifyFirebaseError(error).message));
    unsubscribe = watchCloudBooks(user.uid, (items) => {
      if (!active) return;
      setCloudBooks(items);
      void cacheCloudBooks(user.uid, items);
    }, (error) => setNotice(error.message));
    return () => {
      active = false;
      unsubscribe();
    };
  }, [books, user]);

  useEffect(() => {
    if (!user) {
      setWorkerLinks([]);
      return;
    }
    return watchMacAgents(user.uid, setWorkerLinks, (error) => setNotice(error.message));
  }, [user]);

  useEffect(() => {
    if (!canAdd) return;
    let active = true;
    let timer = 0;
    const refresh = async () => {
      try {
        const status = await getVoiceStatus();
        if (!active) return;
        setVoiceStatus(status);
        if (status.preview.state === "GENERATING") {
          timer = window.setTimeout(() => void refresh(), 5000);
        }
      } catch {
        if (active) setVoiceStatus(null);
      }
    };
    void refresh();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [canAdd, showVoice, voiceStatus?.preview.state]);

  const selectedBook = books.find((book) => book.book_id === selectedBookId);
  const selectedChapter = selectedBook?.chapters.find((chapter) => chapter.chapter_id === selectedChapterId)
    ?? selectedBook?.chapters[0];

  useEffect(() => {
    if (!selectedBook) return;
    let active = true;
    void (async () => {
      const local = await loadProgress(selectedBook.book_id);
      let chosen = local;
      if (user) {
        try {
          const remote = await loadCloudProgress(user.uid, selectedBook.book_id);
          if (remote && remote.version > (local?.cloud_version || 0)) {
            chosen = {
              book_id: remote.book_id,
              chapter_id: remote.chapter_id,
              segment_id: remote.segment_id,
              segment_order: remote.segment_order,
              audio_offset_seconds: remote.audio_offset_seconds,
              cloud_version: remote.version,
              pending_sync: false,
              updated_at: new Date().toISOString(),
            };
            await saveProgress(chosen);
          }
        } catch (error) {
          setNotice(classifyFirebaseError(error).message);
        }
      }
      if (!active) return;
      setSelectedChapterId(chosen?.chapter_id || selectedBook.chapters[0]?.chapter_id || "");
      setSelectedSegmentId(chosen?.segment_id || "");
    })();
    return () => {
      active = false;
    };
  }, [selectedBook, user]);

  async function synchronizeProgress(ownerUid: string, progress: LocalProgress): Promise<void> {
    const input: ProgressInput = {
      book_id: progress.book_id,
      chapter_id: progress.chapter_id,
      segment_id: progress.segment_id,
      segment_order: progress.segment_order || 0,
      audio_offset_seconds: progress.audio_offset_seconds || 0,
    };
    const current = await loadProgress(progress.book_id);
    const result = await saveProgressOptimistically(ownerUid, input, current?.cloud_version || 0);
    if (result.status === "CONFLICT") {
      await saveProgress({
        book_id: result.progress.book_id,
        chapter_id: result.progress.chapter_id,
        segment_id: result.progress.segment_id,
        segment_order: result.progress.segment_order,
        audio_offset_seconds: result.progress.audio_offset_seconds,
        cloud_version: result.progress.version,
        pending_sync: false,
        updated_at: new Date().toISOString(),
      });
      if (selectedBookId === progress.book_id) {
        setSelectedChapterId(result.progress.chapter_id);
        setSelectedSegmentId(result.progress.segment_id);
      }
      setNotice("另一台设备有更新的阅读位置，已安全恢复到最新位置。");
      return;
    }
    const latest = await loadProgress(progress.book_id);
    await saveProgress({
      ...(latest || progress),
      cloud_version: result.progress.version,
      pending_sync: latest?.segment_id !== progress.segment_id,
    });
  }

  function queueProgressSync(ownerUid: string, progress: LocalProgress) {
    const previous = progressQueues.current.get(progress.book_id) || Promise.resolve();
    const next = previous
      .then(() => synchronizeProgress(ownerUid, progress))
      .catch((error) => setNotice(classifyFirebaseError(error).message));
    progressQueues.current.set(progress.book_id, next);
  }

  useEffect(() => {
    if (!user) return;
    const retry = () => {
      void loadPendingProgress().then((items) => items.forEach((item) => queueProgressSync(user.uid, item)));
    };
    retry();
    window.addEventListener("online", retry);
    return () => window.removeEventListener("online", retry);
  }, [user]);

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
      if (user) {
        try {
          await syncBookMetadata(user.uid, book);
          await markBookMetadataSynced(user.uid, book);
        } catch (error) {
          setNotice(`${classifyFirebaseError(error).message} 《${book.title}》已安全保存在本机。`);
          return;
        }
      }
      setNotice(`《${book.title}》已加入书架。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "添加书籍失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function logIn() {
    try {
      await signInWithGoogle();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "登录没有完成，请重试。");
    }
  }

  async function connectMacAutomatically() {
    if (!user) return;
    setBusy(true);
    try {
      const pairing = await startPairingOnMac();
      await pairMacAgent(user.uid, pairing.code);
      setShowPairing(false);
      setPairingCode("");
      setNotice("这台 Mac 已连接，以后会自动接收语音生成任务。");
    } catch (error) {
      setShowPairing(true);
      setNotice(error instanceof Error ? error.message : "自动连接失败，可以输入 Mac 显示的六位码。");
    } finally {
      setBusy(false);
    }
  }

  async function connectWithCode() {
    if (!user) return;
    setBusy(true);
    try {
      await pairMacAgent(user.uid, pairingCode);
      setShowPairing(false);
      setPairingCode("");
      setNotice("这台 Mac 已连接。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "配对码无效，请重新生成。");
    } finally {
      setBusy(false);
    }
  }

  async function disconnectMac() {
    const activeMac = workerLinks.find((link) => !link.revoked_at);
    if (!user || !activeMac) return;
    setBusy(true);
    try {
      await revokeMacAgent(user.uid, activeMac.worker_uid);
      setShowDisconnectMac(false);
      setNotice("这台 Mac 已断开，不会再接收新的语音生成任务。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "暂时无法断开这台 Mac，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function chooseVoice() {
    if (!voiceTranscript.trim()) {
      setNotice("请先填写录音中说的准确文字。");
      return;
    }
    setBusy(true);
    setNotice("正在等待你选择 10 到 30 秒的声音录音...");
    try {
      const status = await chooseVoiceOnMac(voiceTranscript);
      if (status) {
        setVoiceStatus(status);
        setNotice("声音已安全保存在本机，现在可以生成试听。");
      } else {
        setNotice("没有选择声音文件。");
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "声音设置没有完成。");
    } finally {
      setBusy(false);
    }
  }

  async function generateVoicePreview() {
    setBusy(true);
    try {
      setVoiceStatus(await startVoicePreview());
      setNotice("正在用你的声音生成约一分钟试听，可以先做别的事。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "试听没有开始。");
    } finally {
      setBusy(false);
    }
  }

  async function acceptVoice() {
    if (!voiceStatus?.voice_version) return;
    setBusy(true);
    try {
      setVoiceStatus(await confirmVoice(voiceStatus.voice_version));
      setShowVoice(false);
      setNotice("声音已确认，以后的书会自动复用这个声音和语气。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "声音确认没有完成。");
    } finally {
      setBusy(false);
    }
  }

  async function generateFiveHours() {
    if (!user || !selectedBook || !voiceStatus?.voice_version || !voiceStatus.confirmed) {
      setShowVoice(true);
      setNotice("请先设置并确认你的声音。");
      return;
    }
    setBusy(true);
    try {
      const count = await requestFiveHourGeneration(user.uid, selectedBook, voiceStatus.voice_version);
      setNotice(count
        ? `已安排 ${count} 个音频块，第一块完成后就能开始听。`
        : "这批音频已经安排过，不会重复生成。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "生成任务没有创建成功。");
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
    void (async () => {
      const existing = await loadProgress(selectedBook.book_id);
      const progress: LocalProgress = {
        book_id: selectedBook.book_id,
        chapter_id: selectedChapter.chapter_id,
        segment_id: segment.segment_id,
        segment_order: segment.order,
        audio_offset_seconds: 0,
        cloud_version: existing?.cloud_version || 0,
        pending_sync: Boolean(user),
        updated_at: new Date().toISOString(),
      };
      await saveProgress(progress);
      if (user) queueProgressSync(user.uid, progress);
    })();
  }

  const cloudOnlyBooks = cloudBooks.filter((cloudBook) => !books.some((book) => book.book_id === cloudBook.book_id));
  const activeMac = workerLinks.find((link) => !link.revoked_at);

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => setSelectedBookId("")} aria-label="返回书架">
          <span className="brand-seal">听</span>
          <span><b>听见书页</b><small>边听，边读，记住每一处停留</small></span>
        </button>
        <div className="topbar-actions">
          <span className={`sync-state ${user ? "is-online" : ""}`}><i /> {user ? "云同步已开启" : "本机书架"}</span>
          {firebaseConfigured && !user && <button className="quiet-button header-button" onClick={() => void logIn()}>登录同步</button>}
          {user && canAdd && !activeMac && <button className="quiet-button header-button" onClick={() => void connectMacAutomatically()}>连接这台 Mac</button>}
          {user && canAdd && activeMac && <button className="quiet-button header-button is-connected" onClick={() => setShowDisconnectMac(true)}>Mac 已连接</button>}
          {canAdd && <button className="quiet-button header-button" onClick={() => setShowVoice(true)}>{voiceStatus?.confirmed ? "声音已设置" : "我的声音"}</button>}
          {user && <button className="account-button" onClick={() => void signOutCurrentUser()} title="点击退出登录">{user.photoURL ? <img src={user.photoURL} alt="" /> : "我"}</button>}
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
            <p>{user ? "书架和阅读位置已在设备间同步，书籍正文仍按权利设置安全保存。" : "书和进度会先安全留在这台设备；登录后可在手机继续。"}</p>
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
                <span className="book-meta">{book.chapters.length} 章 · {cloudSummaryFor(book, cloudBooks) ? "已同步" : book.publication_mode === "LOCAL_ONLY" ? "仅本机" : "等待同步"}</span>
              </button>
            ))}
            {cloudOnlyBooks.map((book, index) => (
              <button
                className={`book-card book-card--cloud book-card--tone-${(books.length + index) % 4}`}
                key={book.book_id}
                onClick={() => setNotice(book.publication_mode === "LOCAL_ONLY" ? "这本书的正文只在添加它的 Mac 上，请回到那台 Mac 阅读。" : "这本书已在云端书架中，正文会在发布完成后出现。")}
              >
                <span className="book-spine" />
                <span className="book-format">{book.source_format}</span>
                <span className="book-title">{book.title}</span>
                <span className="book-author">{book.author || "作者未注明"}</span>
                <span className="book-meta">{book.chapter_count} 章 · {book.publication_mode === "LOCAL_ONLY" ? "正文仅在 Mac" : "云端书架"}</span>
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
            {selectedBook.publication_mode === "PUBLIC_RIGHTS_CONFIRMED" && (
              <button
                className="generate-audio-button"
                onClick={() => void generateFiveHours()}
                disabled={busy || !user || !activeMac}
              >
                生成约 5 小时音频
              </button>
            )}
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

      {showPairing && (
        <div className="modal-backdrop" role="presentation">
          <section className="import-modal" role="dialog" aria-modal="true" aria-labelledby="pair-title">
            <span className="modal-kicker">连接语音生成器</span>
            <h2 id="pair-title">连接这台 Mac</h2>
            <p>优先点“自动连接”。如果浏览器拦截了本机访问，再输入 Mac 工具显示的六位码。</p>
            <input
              className="pairing-input"
              inputMode="numeric"
              maxLength={6}
              value={pairingCode}
              onChange={(event) => setPairingCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="六位配对码"
              aria-label="六位配对码"
            />
            <div className="modal-actions">
              <button className="quiet-button" onClick={() => setShowPairing(false)}>稍后再说</button>
              <button className="quiet-button" onClick={() => void connectMacAutomatically()} disabled={busy}>自动连接</button>
              <button className="primary-button" onClick={() => void connectWithCode()} disabled={busy || pairingCode.length !== 6}>使用配对码</button>
            </div>
          </section>
        </div>
      )}

      {showDisconnectMac && activeMac && (
        <div className="modal-backdrop" role="presentation">
          <section className="import-modal" role="dialog" aria-modal="true" aria-labelledby="disconnect-title">
            <span className="modal-kicker">设备管理</span>
            <h2 id="disconnect-title">断开这台 Mac？</h2>
            <p>断开后它不会再接收新的语音生成任务。书架、进度和已经生成的音频不会被删除，以后也可以重新连接。</p>
            <div className="modal-actions">
              <button className="quiet-button" onClick={() => setShowDisconnectMac(false)}>保持连接</button>
              <button className="primary-button" onClick={() => void disconnectMac()} disabled={busy}>确认断开</button>
            </div>
          </section>
        </div>
      )}

      {showVoice && (
        <div className="modal-backdrop" role="presentation">
          <section className="import-modal voice-modal" role="dialog" aria-modal="true" aria-labelledby="voice-title">
            <span className="modal-kicker">只保存在这台 Mac</span>
            <h2 id="voice-title">设置我的声音</h2>
            {!voiceStatus?.configured ? (
              <>
                <p>准备一段 10 到 30 秒的清晰录音，并填写录音中一字不差的对应文字。</p>
                <textarea
                  className="voice-transcript"
                  value={voiceTranscript}
                  onChange={(event) => setVoiceTranscript(event.target.value)}
                  placeholder="在这里填写录音中说的全部文字"
                  rows={5}
                />
                <div className="modal-actions">
                  <button className="quiet-button" onClick={() => setShowVoice(false)}>稍后再说</button>
                  <button className="primary-button" onClick={() => void chooseVoice()} disabled={busy || !voiceTranscript.trim()}>选择录音</button>
                </div>
              </>
            ) : (
              <>
                <p>{voiceStatus.confirmed ? "这个声音已经确认，可以直接用来生成听书音频。" : "录音已准备好。先生成并听一下试听，满意后再确认。"}</p>
                {voiceStatus.preview.state === "GENERATING" && <div className="voice-progress"><i />正在生成试听，完成后这里会自动出现播放器...</div>}
                {voiceStatus.preview_available && voiceStatus.voice_version && (
                  <audio className="voice-audio" controls preload="metadata" src={voicePreviewUrl(voiceStatus.voice_version)} />
                )}
                {voiceStatus.preview.error && <p className="voice-error">{voiceStatus.preview.error}</p>}
                <div className="modal-actions">
                  <button className="quiet-button" onClick={() => { setVoiceStatus(null); setVoiceTranscript(""); }}>换一个录音</button>
                  <button className="quiet-button" onClick={() => setShowVoice(false)}>关闭</button>
                  {!voiceStatus.confirmed && voiceStatus.preview.state !== "GENERATING" && <button className="quiet-button" onClick={() => void generateVoicePreview()} disabled={busy}>生成试听</button>}
                  {!voiceStatus.confirmed && voiceStatus.preview_available && <button className="primary-button" onClick={() => void acceptVoice()} disabled={busy}>满意，使用这个声音</button>}
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
