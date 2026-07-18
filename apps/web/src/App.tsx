import type { Chapter, ParsedBook, TextSegment } from "@audiobook/contracts";
import type { User } from "firebase/auth";
import { useEffect, useRef, useState } from "react";

import { AudioManager } from "./AudioManager";
import {
  chooseBookOnMac,
  chooseVoiceOnMac,
  confirmVoice,
  getVoiceStatus,
  loadVoicePreview,
  startPairingOnMac,
  startVoicePreview,
  type VoiceStatus,
} from "./agent";
import { signInWithGoogle, signOutCurrentUser, watchAuth } from "./auth";
import {
  loadCloudProgress,
  loadRemoteBook,
  prioritizeActiveBook,
  requestAudioRepair,
  requestFiveHourGeneration,
  saveBookmark,
  saveProgressOptimistically,
  syncBookMetadata,
  watchAudioChunks,
  watchBookmarks,
  watchCloudBooks,
  type AudioChunk,
  type CloudBookmark,
  type CloudBookSummary,
  type ProgressInput,
} from "./cloud";
import { registerCurrentDevice } from "./device";
import { classifyFirebaseError, type SyncError } from "./firebase-errors";
import { firebaseIsConfigured } from "./firebase";
import {
  pairMacAgent,
  revokeMacAgent,
  watchMacAgents,
  workerIsOnline,
  type WorkerLink,
} from "./pairing";
import { PlayerDock, type PlayerJumpRequest, type PlayerPosition } from "./PlayerDock";
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
import { cloudSyncIsPaused, cloudSyncPauseMessage } from "./usage";
import { SystemStatus } from "./SystemStatus";

const DEMO_BOOK_ID = "356fc83a-1b37-5571-bb94-9d168a6a7c2f";
const E2E_PLAYER_MODE = import.meta.env.DEV
  && new URLSearchParams(window.location.search).get("e2e") === "player";

function e2eBooks(items: ParsedBook[]): ParsedBook[] {
  if (!E2E_PLAYER_MODE || !items[0]) return items;
  const source = items[0];
  const second: ParsedBook = {
    ...source,
    book_id: "e2e-second-book",
    title: "山窗小札 · 第二册",
    source_sha256: "e".repeat(64),
    chapters: source.chapters.map((chapter) => ({
      ...chapter,
      chapter_id: `e2e-${chapter.chapter_id}`,
      segments: chapter.segments.map((segment) => ({
        ...segment,
        segment_id: `e2e-${segment.segment_id}`,
        chapter_id: `e2e-${chapter.chapter_id}`,
      })),
    })),
  };
  return [source, second];
}

function e2eAudioChunks(book: ParsedBook): AudioChunk[] {
  if (!E2E_PLAYER_MODE) return [];
  return book.chapters.slice(0, 2).map((chapter, index) => {
    const segment = chapter.segments[0]!;
    const chunkId = `e2e-${book.book_id}-${index}`;
    const timeline = encodeURIComponent(JSON.stringify({
      schema_version: 1,
      book_id: book.book_id,
      chunk_id: chunkId,
      chapter_id: chapter.chapter_id,
      duration_seconds: 2,
      segments: [{
        segment_id: segment.segment_id,
        chapter_id: chapter.chapter_id,
        segment_order: segment.order,
        start_seconds: 0,
        end_seconds: 2,
      }],
    }));
    return {
      owner_uid: "e2e-owner",
      task_id: `e2e-task-${index}`,
      book_id: book.book_id,
      chunk_id: chunkId,
      chapter_id: chapter.chapter_id,
      status: "READY",
      start_segment_id: segment.segment_id,
      end_segment_id: segment.segment_id,
      duration_seconds: 2,
      asset_id: index + 1,
      asset_url: `https://e2e.invalid/synthetic-tone.m4a?book=${book.book_id}&chunk=${index}`,
      sha256: "",
      byte_size: 20_000,
      timeline_asset_id: index + 101,
      timeline_url: `data:application/json,${timeline}`,
      timeline_sha256: "",
      voice_version: "e2e-synthetic",
      deletion_generation: 0,
    };
  });
}

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
  const [showAudioManager, setShowAudioManager] = useState(false);
  const [showSystemStatus, setShowSystemStatus] = useState(false);
  const [voiceTranscript, setVoiceTranscript] = useState("");
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null);
  const [voicePreviewObjectUrl, setVoicePreviewObjectUrl] = useState("");
  const [pairingCode, setPairingCode] = useState("");
  const [workerLinks, setWorkerLinks] = useState<WorkerLink[]>([]);
  const [audioChunks, setAudioChunks] = useState<AudioChunk[]>([]);
  const [bookmarks, setBookmarks] = useState<CloudBookmark[]>([]);
  const [resumeProgress, setResumeProgress] = useState<LocalProgress | null>(null);
  const [jumpRequest, setJumpRequest] = useState<PlayerJumpRequest | null>(null);
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [busy, setBusy] = useState(true);
  const [notice, setNotice] = useState("");
  const [pageVisible, setPageVisible] = useState(() => !document.hidden);
  const [cloudSyncPaused, setCloudSyncPaused] = useState(() => cloudSyncIsPaused());
  const progressQueues = useRef(new Map<string, Promise<void>>());
  const currentProgress = useRef<LocalProgress | null>(null);
  const cloudBooksRef = useRef<CloudBookSummary[]>([]);
  const jumpSequence = useRef(0);
  const firebaseConfigured = firebaseIsConfigured();

  useEffect(() => watchAuth(setUser), []);

  useEffect(() => {
    const updateVisibility = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  useEffect(() => {
    const pauseSync = () => {
      setCloudSyncPaused(true);
      setNotice(cloudSyncPauseMessage());
    };
    window.addEventListener("audiobook-cloud-sync-paused", pauseSync);
    return () => window.removeEventListener("audiobook-cloud-sync-paused", pauseSync);
  }, []);

  function handleSyncError(error: SyncError) {
    if (error.kind === "FREE_QUOTA") setCloudSyncPaused(true);
    setNotice(error.message);
  }

  useEffect(() => {
    let active = true;
    loadBooks()
      .then((items) => {
        if (!active) return;
        const visibleBooks = e2eBooks(items);
        setBooks(visibleBooks);
        setSelectedBookId((current) => current || visibleBooks[0]?.book_id || "");
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
    if (!user || !pageVisible || cloudSyncPaused) {
      setCloudBooks([]);
      if (cloudSyncPaused) setNotice(cloudSyncPauseMessage());
      return;
    }
    let active = true;
    let unsubscribe: () => void = () => {};
    void loadCachedCloudBooks(user.uid).then((cached) => active && setCloudBooks(cached));
    void registerCurrentDevice(user.uid).catch((error) => handleSyncError(classifyFirebaseError(error)));
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
    }, handleSyncError);
    return () => {
      active = false;
      unsubscribe();
    };
  }, [books, cloudSyncPaused, pageVisible, user]);

  useEffect(() => {
    if (!user || !pageVisible || cloudSyncPaused) {
      setWorkerLinks([]);
      return;
    }
    return watchMacAgents(user.uid, setWorkerLinks, handleSyncError);
  }, [cloudSyncPaused, pageVisible, user]);

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

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    if (!showVoice || !voiceStatus?.preview_available || !voiceStatus.voice_version) {
      setVoicePreviewObjectUrl("");
      return () => undefined;
    }
    void loadVoicePreview(voiceStatus.voice_version)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setVoicePreviewObjectUrl(objectUrl);
      })
      .catch((error) => active && setNotice(
        error instanceof Error ? error.message : "试听音频暂时无法读取。",
      ));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [showVoice, voiceStatus?.preview_available, voiceStatus?.voice_version]);

  const selectedBook = books.find((book) => book.book_id === selectedBookId);
  const selectedChapter = selectedBook?.chapters.find((chapter) => chapter.chapter_id === selectedChapterId)
    ?? selectedBook?.chapters[0];
  const activeMac = workerLinks.find((link) => !link.revoked_at);
  const macOnline = E2E_PLAYER_MODE || workerIsOnline(activeMac);

  useEffect(() => {
    cloudBooksRef.current = cloudBooks;
  }, [cloudBooks]);

  useEffect(() => {
    if (E2E_PLAYER_MODE && selectedBook) {
      setAudioChunks(e2eAudioChunks(selectedBook));
      setBookmarks([]);
      return;
    }
    if (!user || !selectedBook || !pageVisible || cloudSyncPaused) {
      setAudioChunks([]);
      setBookmarks([]);
      return;
    }
    const stopAudio = watchAudioChunks(
      user.uid,
      selectedBook.book_id,
      setAudioChunks,
      handleSyncError,
    );
    const stopBookmarks = watchBookmarks(
      user.uid,
      selectedBook.book_id,
      setBookmarks,
      handleSyncError,
    );
    return () => {
      stopAudio();
      stopBookmarks();
    };
  }, [cloudSyncPaused, pageVisible, selectedBook, user]);

  useEffect(() => {
    if (!user || !selectedBook || cloudSyncPaused || selectedBook.book_id === DEMO_BOOK_ID) return;
    const refreshPriority = () => {
      void prioritizeActiveBook(user.uid, selectedBook.book_id, cloudBooksRef.current)
        .catch((error) => setNotice(classifyFirebaseError(error).message));
    };
    refreshPriority();
    const timer = window.setInterval(refreshPriority, 6 * 60 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [cloudSyncPaused, selectedBook, user]);

  useEffect(() => {
    if (!selectedBook) {
      currentProgress.current = null;
      setResumeProgress(null);
      return;
    }
    let active = true;
    void (async () => {
      const local = await loadProgress(selectedBook.book_id);
      let chosen = local;
      if (user && !cloudSyncPaused) {
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
      currentProgress.current = chosen || null;
      setResumeProgress(chosen || null);
    })();
    return () => {
      active = false;
    };
  }, [cloudSyncPaused, selectedBook, user]);

  useEffect(() => {
    if (!selectedSegmentId) return;
    const element = document.querySelector<HTMLElement>(`[data-segment-id="${selectedSegmentId}"]`);
    if (!element) return;
    const bounds = element.getBoundingClientRect();
    if (bounds.top < 100 || bounds.bottom > window.innerHeight - 130) {
      element.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [selectedChapterId, selectedSegmentId]);

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
      const restored: LocalProgress = {
        book_id: result.progress.book_id,
        chapter_id: result.progress.chapter_id,
        segment_id: result.progress.segment_id,
        segment_order: result.progress.segment_order,
        audio_offset_seconds: result.progress.audio_offset_seconds,
        cloud_version: result.progress.version,
        pending_sync: false,
        updated_at: new Date().toISOString(),
      };
      await saveProgress(restored);
      if (selectedBookId === progress.book_id) {
        currentProgress.current = restored;
        setResumeProgress(restored);
        setSelectedChapterId(result.progress.chapter_id);
        setSelectedSegmentId(result.progress.segment_id);
      }
      setNotice("另一台设备有更新的阅读位置，已安全恢复到最新位置。");
      return;
    }
    const latest = await loadProgress(progress.book_id);
    const saved: LocalProgress = {
      ...(latest || progress),
      cloud_version: result.progress.version,
      pending_sync: latest?.segment_id !== progress.segment_id,
    };
    await saveProgress(saved);
    if (selectedBookId === progress.book_id) {
      currentProgress.current = saved;
    }
  }

  function queueProgressSync(ownerUid: string, progress: LocalProgress) {
    const previous = progressQueues.current.get(progress.book_id) || Promise.resolve();
    const next = previous
      .then(() => synchronizeProgress(ownerUid, progress))
      .catch((error) => setNotice(classifyFirebaseError(error).message));
    progressQueues.current.set(progress.book_id, next);
  }

  useEffect(() => {
    if (!user || cloudSyncPaused) return;
    const retry = () => {
      void loadPendingProgress().then((items) => items.forEach((item) => queueProgressSync(user.uid, item)));
    };
    retry();
    window.addEventListener("online", retry);
    return () => window.removeEventListener("online", retry);
  }, [cloudSyncPaused, user]);

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

  async function openRemoteBook(book: CloudBookSummary) {
    if (!user) {
      setNotice("请先登录，私有书只允许你的账号读取。");
      return;
    }
    setBusy(true);
    try {
      const downloaded = await loadRemoteBook(user.uid, book);
      await saveBook(downloaded);
      setBooks((current) => [downloaded, ...current.filter((item) => item.book_id !== downloaded.book_id)]);
      setSelectedBookId(downloaded.book_id);
      setNotice(`《${downloaded.title}》已安全载入，可以边听边看。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "手机正文暂时无法读取。");
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

  async function persistPlayerPosition(position: PlayerPosition, syncCloud: boolean) {
    if (!selectedBook) return;
    const existing = currentProgress.current?.book_id === selectedBook.book_id
      ? currentProgress.current
      : await loadProgress(selectedBook.book_id);
    const progress: LocalProgress = {
      book_id: selectedBook.book_id,
      chapter_id: position.chapterId,
      segment_id: position.segmentId,
      segment_order: position.segmentOrder,
      audio_offset_seconds: position.audioOffsetSeconds,
      cloud_version: existing?.cloud_version || 0,
      pending_sync: Boolean(user && syncCloud),
      updated_at: new Date().toISOString(),
    };
    currentProgress.current = progress;
    await saveProgress(progress);
    if (user && syncCloud) queueProgressSync(user.uid, progress);
  }

  function requestPlayerJump(segmentId: string, autoplay = false) {
    jumpSequence.current += 1;
    setJumpRequest({ key: jumpSequence.current, segmentId, autoplay });
  }

  async function addPlayerBookmark(position: PlayerPosition) {
    if (!user || !selectedBook) {
      setNotice("登录同步后才能在手机和 Mac 之间保存书签。");
      return;
    }
    try {
      await saveBookmark(
        user.uid,
        selectedBook.book_id,
        position.chapterId,
        position.segmentId,
      );
      setNotice("书签已保存，可以在其他设备打开。");
    } catch (error) {
      setNotice(classifyFirebaseError(error).message);
    }
  }

  async function repairAudio(chunk: AudioChunk) {
    if (!user) return;
    try {
      await requestAudioRepair(user.uid, chunk);
      setNotice(macOnline ? "已安排重新检查这段音频。" : "修复请求已保存，Mac 开机后会自动处理。");
    } catch (error) {
      setNotice(classifyFirebaseError(error).message);
    }
  }

  function openChapter(chapter: Chapter) {
    setSelectedChapterId(chapter.chapter_id);
    const segmentId = chapter.segments[0]?.segment_id || "";
    setSelectedSegmentId(segmentId);
    if (segmentId) requestPlayerJump(segmentId);
  }

  function markPosition(segment: TextSegment) {
    if (!selectedBook || !selectedChapter) return;
    setSelectedSegmentId(segment.segment_id);
    requestPlayerJump(segment.segment_id);
    void persistPlayerPosition({
      chapterId: selectedChapter.chapter_id,
      segmentId: segment.segment_id,
      segmentOrder: segment.order,
      audioOffsetSeconds: 0,
    }, true);
  }

  const cloudOnlyBooks = cloudBooks.filter((cloudBook) => !books.some((book) => book.book_id === cloudBook.book_id));

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => setSelectedBookId("")} aria-label="返回书架">
          <span className="brand-seal">听</span>
          <span><b>听见书页</b><small>边听，边读，记住每一处停留</small></span>
        </button>
        <div className="topbar-actions">
          <span className={`sync-state ${user && !cloudSyncPaused ? "is-online" : ""}`}><i /> {cloudSyncPaused ? "免费额度保护中" : user ? "云同步已开启" : "本机书架"}</span>
          {firebaseConfigured && !user && <button className="quiet-button header-button mobile-login-button" onClick={() => void logIn()}>登录同步</button>}
          {user && canAdd && !activeMac && <button className="quiet-button header-button" onClick={() => void connectMacAutomatically()}>连接这台 Mac</button>}
          {user && canAdd && activeMac && <button className="quiet-button header-button is-connected" onClick={() => setShowDisconnectMac(true)}>Mac 已连接</button>}
          {canAdd && <button className="quiet-button header-button" onClick={() => setShowVoice(true)}>{voiceStatus?.confirmed ? "声音已设置" : "我的声音"}</button>}
          {(user || E2E_PLAYER_MODE) && <button className="quiet-button header-button" onClick={() => setShowAudioManager(true)}>音频空间</button>}
          <button className="quiet-button header-button" onClick={() => setShowSystemStatus(true)}>系统状态</button>
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
          <button className="shelf-status-button" onClick={() => setShowSystemStatus(true)}>检查系统状态</button>
          {(user || E2E_PLAYER_MODE) && (
            <button className="shelf-audio-button" onClick={() => setShowAudioManager(true)}>
              管理已生成音频
            </button>
          )}
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
                <span className="book-meta">{book.chapters.length} 章 · {cloudSummaryFor(book, cloudBooks) ? (book.publication_mode === "LOCAL_ONLY" ? "账号私有" : "已同步") : book.publication_mode === "LOCAL_ONLY" ? "等待私有同步" : "等待同步"}</span>
              </button>
            ))}
            {cloudOnlyBooks.map((book, index) => (
              <button
                className={`book-card book-card--cloud book-card--tone-${(books.length + index) % 4}`}
                key={book.book_id}
                onClick={() => void openRemoteBook(book)}
              >
                <span className="book-spine" />
                <span className="book-format">{book.source_format}</span>
                <span className="book-title">{book.title}</span>
                <span className="book-author">{book.author || "作者未注明"}</span>
                <span className="book-meta">{book.chapter_count} 章 · {book.publication_mode === "LOCAL_ONLY" ? (book.text_status === "READY" ? "账号私有" : "等待生成") : "云端书架"}</span>
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
              {selectedBook.publication_mode === "LOCAL_ONLY" ? "仅你的登录账号可访问" : "已确认可公开"}
            </div>
            {canAdd ? (
              <button
                className="generate-audio-button"
                onClick={() => void generateFiveHours()}
                disabled={busy || !user || !activeMac}
              >
                {selectedBook.publication_mode === "LOCAL_ONLY" ? "私密生成约 5 小时音频" : "生成约 5 小时音频"}
              </button>
            ) : (
              <p className="mobile-generation-hint">请在 Mac 上生成音频，已生成的内容可以直接播放。</p>
            )}
            {(user || E2E_PLAYER_MODE) && (
              <button className="manage-audio-button" onClick={() => setShowAudioManager(true)}>
                管理已生成音频
              </button>
            )}
            {bookmarks.length > 0 && (
              <div className="bookmark-list" aria-label="书签">
                <b>书签 {bookmarks.length}</b>
                {bookmarks.slice(0, 6).map((bookmark, index) => (
                  <button
                    key={bookmark.bookmark_id}
                    onClick={() => requestPlayerJump(bookmark.segment_id)}
                  >
                    <span>{index + 1}</span>
                    {selectedBook.chapters.find((chapter) => chapter.chapter_id === bookmark.chapter_id)?.title || "已保存位置"}
                  </button>
                ))}
              </div>
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
                  data-segment-id={segment.segment_id}
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
        <PlayerDock
          book={selectedBook}
          ownerUid={user?.uid || ""}
          chunks={audioChunks}
          resumeSegmentId={resumeProgress?.segment_id || ""}
          resumeOffsetSeconds={resumeProgress?.audio_offset_seconds || 0}
          jumpRequest={jumpRequest}
          macOnline={macOnline}
          onHighlight={(chapterId, segmentId) => {
            if (chapterId) setSelectedChapterId(chapterId);
            setSelectedSegmentId(segmentId);
          }}
          onPosition={(position, syncCloud) => void persistPlayerPosition(position, syncCloud)}
          onBookmark={(position) => void addPlayerBookmark(position)}
          onRepair={(chunk) => void repairAudio(chunk)}
          onNotice={setNotice}
        />
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
              <span><b>我确认有权公开传播这本书</b><small>不勾选时会标记为私有，只存入你的登录账号专属区域，绝不会上传到公开 GitHub。</small></span>
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
                {voiceStatus.preview_available && voiceStatus.voice_version && voicePreviewObjectUrl && (
                  <audio className="voice-audio" controls preload="metadata" src={voicePreviewObjectUrl} />
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

      {showAudioManager && (user || E2E_PLAYER_MODE) && (
        <AudioManager
          ownerUid={user?.uid || "e2e-owner"}
          books={books}
          cloudBooks={cloudBooks}
          initialChunks={E2E_PLAYER_MODE ? books.flatMap(e2eAudioChunks) : undefined}
          onClose={() => setShowAudioManager(false)}
          onNotice={setNotice}
        />
      )}

      {showSystemStatus && (
        <SystemStatus
          user={user}
          activeMac={activeMac}
          canInspectLocalMac={canAdd}
          onClose={() => setShowSystemStatus(false)}
          onNotice={setNotice}
        />
      )}
    </div>
  );
}
