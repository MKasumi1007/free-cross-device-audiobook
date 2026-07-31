import type { Chapter, ParsedBook, TextSegment } from "@audiobook/contracts";
import type { User } from "firebase/auth";
import { useEffect, useRef, useState } from "react";

import { AudioManager } from "./AudioManager";
import {
  chooseBookOnMac,
  chooseVoiceOnMac,
  confirmVoice,
  enqueueLocalGeneration,
  getLocalGenerationStatus,
  getVoiceStatus,
  loadVoicePreview,
  reorderLocalGenerationTasks,
  startPairingOnMac,
  startVoicePreview,
  updateLocalGenerationTasks,
  type LocalGenerationStatus,
  type VoiceStatus,
} from "./agent";
import { signInWithGoogle, signOutCurrentUser, watchAuth } from "./auth";
import {
  buildGenerationQueue,
  enqueueGenerationChapters,
  generationTaskIsLive,
  loadVoiceGenerationProfile,
  loadCloudProgress,
  loadRemoteBook,
  markBookListened,
  mergeAudioChunks,
  mergeGenerationTasks,
  reorderGenerationQueue,
  requestBookDeletion,
  requestAudioRepair,
  saveBookmark,
  saveProgressOptimistically,
  saveVoiceGenerationProfile,
  syncBookMetadata,
  updateGenerationQueueItem,
  watchAudioChunks,
  watchBookmarks,
  watchCloudBooks,
  watchGenerationTasks,
  type AudioChunk,
  type CloudBookmark,
  type CloudBookSummary,
  type GenerationQueueItem,
  type GenerationTaskSummary,
  type ProgressInput,
} from "./cloud";
import { registerCurrentDevice } from "./device";
import { classifyFirebaseError, type SyncError } from "./firebase-errors";
import { firebaseIsConfigured } from "./firebase";
import { GenerationQueue } from "./GenerationQueue";
import { chapterDirectoryStatus } from "./chapter-directory-status";
import {
  pairMacAgent,
  revokeMacAgent,
  watchMacAgents,
  workerIsOnline,
  type WorkerLink,
} from "./pairing";
import { PlayerDock, type PlayerJumpRequest, type PlayerPosition } from "./PlayerDock";
import { canAddBooks, currentPlatformSignals } from "./platform";
import { firstPlayableSegmentIdForChapter } from "./player";
import {
  bookMetadataNeedsSync,
  cacheCloudBooks,
  deleteLocalBook,
  hideBook,
  loadBooks,
  loadCachedCloudBooks,
  loadHiddenBooks,
  loadPendingProgress,
  loadProgress,
  markBookMetadataSynced,
  saveBook,
  saveProgress,
  unhideBook,
  type HiddenBook,
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

interface BookRemovalTarget {
  book_id: string;
  title: string;
  author: string;
}

export function App() {
  const [books, setBooks] = useState<ParsedBook[]>([]);
  const [cloudBooks, setCloudBooks] = useState<CloudBookSummary[]>([]);
  const [hiddenBooks, setHiddenBooks] = useState<HiddenBook[]>([]);
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
  const [showGenerationQueue, setShowGenerationQueue] = useState(false);
  const [showSystemStatus, setShowSystemStatus] = useState(false);
  const [showHiddenBooks, setShowHiddenBooks] = useState(false);
  const [bookMenuId, setBookMenuId] = useState("");
  const [deletionTarget, setDeletionTarget] = useState<BookRemovalTarget | null>(null);
  const [deletionStep, setDeletionStep] = useState<1 | 2>(1);
  const [deletionConfirmation, setDeletionConfirmation] = useState("");
  const [voiceTranscript, setVoiceTranscript] = useState("");
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null);
  const [voicePreviewObjectUrl, setVoicePreviewObjectUrl] = useState("");
  const [pairingCode, setPairingCode] = useState("");
  const [workerLinks, setWorkerLinks] = useState<WorkerLink[]>([]);
  const [cloudAudioChunks, setCloudAudioChunks] = useState<AudioChunk[]>([]);
  const [cloudGenerationTasks, setCloudGenerationTasks] = useState<GenerationTaskSummary[]>([]);
  const [localGeneration, setLocalGeneration] = useState<LocalGenerationStatus | null>(null);
  const [cloudVoiceVersion, setCloudVoiceVersion] = useState("");
  const [bookmarks, setBookmarks] = useState<CloudBookmark[]>([]);
  const [resumeProgress, setResumeProgress] = useState<LocalProgress | null>(null);
  const [jumpRequest, setJumpRequest] = useState<PlayerJumpRequest | null>(null);
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [busy, setBusy] = useState(true);
  const [notice, setNotice] = useState("");
  const [pageVisible, setPageVisible] = useState(() => !document.hidden);
  const [cloudSyncPaused, setCloudSyncPaused] = useState(() => cloudSyncIsPaused());
  const [directoryActionBusy, setDirectoryActionBusy] = useState("");
  const progressQueues = useRef(new Map<string, Promise<void>>());
  const currentProgress = useRef<LocalProgress | null>(null);
  const jumpSequence = useRef(0);
  const localSyncRequested = useRef(new Set<string>());
  const localMigrationRequested = useRef("");
  const firebaseConfigured = firebaseIsConfigured();

  useEffect(() => watchAuth(setUser), []);

  useEffect(() => {
    const updateVisibility = () => setPageVisible(!document.hidden);
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  useEffect(() => {
    if (!cloudSyncPaused) return;
    const checkForNewDay = () => {
      if (!cloudSyncIsPaused()) setCloudSyncPaused(false);
    };
    const timer = window.setInterval(checkForNewDay, 60_000);
    return () => window.clearInterval(timer);
  }, [cloudSyncPaused]);

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
    Promise.all([loadBooks(), loadHiddenBooks()])
      .then(([items, hidden]) => {
        if (!active) return;
        const loadedBooks = e2eBooks(items);
        const hiddenIds = new Set(hidden.map((book) => book.book_id));
        const firstVisible = loadedBooks.find((book) => !hiddenIds.has(book.book_id));
        setBooks(loadedBooks);
        setHiddenBooks(hidden);
        setSelectedBookId((current) => current || firstVisible?.book_id || "");
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
    let receivedLiveBooks = false;
    let unsubscribe: () => void = () => {};
    void loadCachedCloudBooks(user.uid).then((cached) => {
      if (active && !receivedLiveBooks) setCloudBooks(cached);
    });
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
      receivedLiveBooks = true;
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
    if (!user || !pageVisible || cloudSyncPaused) {
      setCloudGenerationTasks([]);
      if (!user) setCloudVoiceVersion("");
      return;
    }
    let active = true;
    void loadVoiceGenerationProfile(user.uid)
      .then((profile) => {
        if (active && profile?.confirmed) setCloudVoiceVersion(profile.voice_version);
      })
      .catch((error) => handleSyncError(classifyFirebaseError(error)));
    const stopTasks = watchGenerationTasks(user.uid, setCloudGenerationTasks, handleSyncError);
    return () => {
      active = false;
      stopTasks();
    };
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
    if (!canAdd || !pageVisible) {
      setLocalGeneration(null);
      return;
    }
    let active = true;
    const refresh = async () => {
      try {
        const status = await getLocalGenerationStatus();
        if (active) setLocalGeneration(status);
      } catch {
        if (active) setLocalGeneration(null);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [canAdd, pageVisible]);

  useEffect(() => {
    if (!user || cloudSyncPaused || !localGeneration) return;
    const pending = localGeneration.tasks.filter((task) => (
      task.owner_uid === user.uid
      && task.status === "READY"
      && task.sync_status !== "SYNCED"
      && !localSyncRequested.current.has(task.task_id)
    ));
    if (!pending.length) return;
    const byVoice = new Map<string, GenerationTaskSummary[]>();
    for (const task of pending) {
      const voiceVersion = task.voice_version || "";
      if (!voiceVersion) continue;
      byVoice.set(voiceVersion, [...(byVoice.get(voiceVersion) || []), task]);
      localSyncRequested.current.add(task.task_id);
    }
    void (async () => {
      for (const [voiceVersion, tasks] of byVoice) {
        const taskIds = tasks.map((task) => task.task_id);
        try {
          const selections = books
            .map((book) => ({
              book,
              chapter_ids: [...new Set(tasks
                .filter((task) => task.book_id === book.book_id)
                .map((task) => task.chapter_id || "")
                .filter(Boolean))],
            }))
            .filter((selection) => selection.chapter_ids.length > 0);
          if (selections.length) {
            await enqueueGenerationChapters(user.uid, selections, voiceVersion);
          }
        } catch (error) {
          taskIds.forEach((taskId) => localSyncRequested.current.delete(taskId));
          handleSyncError(classifyFirebaseError(error));
        }
      }
    })();
  }, [books, cloudSyncPaused, localGeneration, user]);

  useEffect(() => {
    if (!user || !voiceStatus?.confirmed || !voiceStatus.voice_version || cloudSyncPaused) return;
    setCloudVoiceVersion(voiceStatus.voice_version);
    void saveVoiceGenerationProfile(user.uid, voiceStatus.voice_version)
      .catch((error) => handleSyncError(classifyFirebaseError(error)));
  }, [cloudSyncPaused, user, voiceStatus?.confirmed, voiceStatus?.voice_version]);

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

  const hiddenBookIds = new Set(hiddenBooks.map((book) => book.book_id));
  const visibleBooks = books.filter((book) => !hiddenBookIds.has(book.book_id));
  const visibleCloudBooks = cloudBooks.filter((book) => !hiddenBookIds.has(book.book_id));
  const selectedBook = visibleBooks.find((book) => book.book_id === selectedBookId);
  const selectedChapter = selectedBook?.chapters.find((chapter) => chapter.chapter_id === selectedChapterId)
    ?? selectedBook?.chapters[0];
  const localTasks = user
    ? (localGeneration?.tasks || []).filter((task) => task.owner_uid === user.uid)
    : [];
  const generationTasks = mergeGenerationTasks(cloudGenerationTasks, localTasks);
  const allLocalAudioChunks = user
    ? (localGeneration?.audio_chunks || []).filter((chunk) => chunk.owner_uid === user.uid)
    : [];
  const localAudioChunks = selectedBook
    ? allLocalAudioChunks.filter((chunk) => chunk.book_id === selectedBook.book_id)
    : [];
  const audioChunks = mergeAudioChunks(cloudAudioChunks, localAudioChunks);
  const activeMac = workerLinks.find((link) => !link.revoked_at);
  const macOnline = E2E_PLAYER_MODE
    || workerIsOnline(activeMac)
    || localGeneration !== null
    || generationTasks.some(generationTaskIsLive);
  const effectiveVoiceVersion = voiceStatus?.confirmed && voiceStatus.voice_version
    ? voiceStatus.voice_version
    : cloudVoiceVersion || generationTasks.find((task) => task.voice_version)?.voice_version || "";
  const localQuotaPaused = localGeneration?.worker.state === "FREE_QUOTA_LOCAL_READY"
    || Number(localGeneration?.worker.cloud_backoff_seconds || 0) > 0;
  const localQueueActive = localTasks.some((task) => (
    task.status !== "READY" && task.status !== "CANCELLED"
  ));
  const localFallbackMode = cloudSyncPaused || localQuotaPaused || localQueueActive;
  const generationQueue = buildGenerationQueue(generationTasks, visibleBooks);
  const priorityGenerationItems = generationQueue.filter((item) => (
    item.status === "QUEUED" || item.status === "PAUSED"
  ));
  const queuedChapterCount = generationQueue
    .filter((item) => item.status !== "COMPLETED" && item.status !== "REMOVED")
    .length;

  useEffect(() => {
    if (!user || !macOnline || !localFallbackMode || !effectiveVoiceVersion) return;
    const localTaskIds = new Set(localTasks.map((task) => task.task_id));
    const cloudById = new Map(cloudGenerationTasks.map((task) => [task.task_id, task]));
    const selections = buildGenerationQueue(cloudGenerationTasks, visibleBooks)
      .filter((item) => item.status === "QUEUED" || item.status === "FAILED")
      .map((item) => ({
        book_id: item.book_id,
        chapter_ids: [item.chapter_id],
        task_ids: item.task_ids.filter((taskId) => {
          const task = cloudById.get(taskId);
          return Boolean(
            task
            && !localTaskIds.has(taskId)
            && !["READY", "PAUSED", "CANCELLED"].includes(task.status),
          );
        }),
      }))
      .filter((selection) => selection.task_ids.length > 0);
    if (!selections.length) return;
    const requestKey = selections.flatMap((selection) => selection.task_ids).join(":");
    if (!requestKey || localMigrationRequested.current === requestKey) return;
    localMigrationRequested.current = requestKey;
    void enqueueLocalGeneration(user.uid, selections, effectiveVoiceVersion)
      .then((result) => {
        setNotice(
          `免费额度保护已接管 ${result.chapters} 章，`
          + `这台 Mac 将从现有断点继续生成。`,
        );
      })
      .catch((error) => {
        localMigrationRequested.current = "";
        setNotice(error instanceof Error ? error.message : "旧待生成任务暂时无法转到这台 Mac。");
      });
  }, [
    cloudGenerationTasks,
    effectiveVoiceVersion,
    localFallbackMode,
    localTasks,
    macOnline,
    user,
    visibleBooks,
  ]);

  useEffect(() => {
    if (E2E_PLAYER_MODE && selectedBook) {
      setCloudAudioChunks(e2eAudioChunks(selectedBook));
      setBookmarks([]);
      return;
    }
    if (!user || !selectedBook || !pageVisible || cloudSyncPaused) {
      setCloudAudioChunks([]);
      setBookmarks([]);
      return;
    }
    const stopAudio = watchAudioChunks(
      user.uid,
      selectedBook.book_id,
      setCloudAudioChunks,
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
    void markBookListened(user.uid, selectedBook.book_id)
      .catch((error) => setNotice(classifyFirebaseError(error).message));
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

  async function hideFromShelf(target: BookRemovalTarget) {
    try {
      await hideBook({
        book_id: target.book_id,
        title: target.title,
        author: target.author,
      });
      setHiddenBooks((current) => [{
        book_id: target.book_id,
        title: target.title,
        author: target.author,
        hidden_at: new Date().toISOString(),
      }, ...current.filter((book) => book.book_id !== target.book_id)]);
      if (selectedBookId === target.book_id) setSelectedBookId("");
      setBookMenuId("");
      setNotice(`《${target.title}》已从这台设备的书架隐藏，可在“已隐藏书籍”中恢复。`);
    } catch {
      setNotice("这本书暂时无法隐藏，请刷新后再试。");
    }
  }

  async function restoreHiddenBook(book: HiddenBook) {
    try {
      await unhideBook(book.book_id);
      setHiddenBooks((current) => current.filter((item) => item.book_id !== book.book_id));
      setNotice(`《${book.title}》已恢复到书架。`);
    } catch {
      setNotice("这本书暂时无法恢复，请刷新后再试。");
    }
  }

  function beginPermanentDeletion(target: BookRemovalTarget) {
    setBookMenuId("");
    setDeletionTarget(target);
    setDeletionStep(1);
    setDeletionConfirmation("");
  }

  async function permanentlyDeleteBook() {
    if (!user || !deletionTarget || deletionConfirmation !== "永久删除") return;
    const target = deletionTarget;
    setBusy(true);
    try {
      const result = await requestBookDeletion(user.uid, target.book_id);
      await deleteLocalBook(target.book_id, user.uid);
      setBooks((current) => current.filter((book) => book.book_id !== target.book_id));
      setCloudBooks((current) => current.filter((book) => book.book_id !== target.book_id));
      setHiddenBooks((current) => current.filter((book) => book.book_id !== target.book_id));
      setSelectedBookId("");
      setDeletionTarget(null);
      setDeletionConfirmation("");
      const cleanup = result.queued_audio_chunks > 0
        ? `，并已安排清理 ${result.queued_audio_chunks} 段音频`
        : "";
      setNotice(
        macOnline
          ? `《${target.title}》已从账号永久移除${cleanup}。`
          : `《${target.title}》已从账号移除${cleanup}；Mac 开机后会完成本机文件清理。`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "永久删除没有完成，请稍后重试。");
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
      const confirmed = await confirmVoice(voiceStatus.voice_version);
      setVoiceStatus(confirmed);
      if (user) {
        await saveVoiceGenerationProfile(user.uid, voiceStatus.voice_version);
        setCloudVoiceVersion(voiceStatus.voice_version);
      }
      setShowVoice(false);
      setNotice("声音已确认，以后的书会自动复用这个声音和语气。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "声音确认没有完成。");
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
    const segmentId = selectedBook
      ? firstPlayableSegmentIdForChapter(selectedBook, audioChunks, chapter.chapter_id)
        ?? chapter.segments[0]?.segment_id
        ?? ""
      : "";
    setSelectedSegmentId(segmentId);
    if (segmentId) requestPlayerJump(segmentId);
  }

  async function resumeChapterGeneration(item: GenerationQueueItem) {
    if (!user) return;
    setDirectoryActionBusy(item.queue_id);
    try {
      const localTaskIds = item.task_ids.filter((taskId) => (
        generationTasks.find((task) => task.task_id === taskId)?.execution_mode === "LOCAL"
      ));
      const cloudTaskIds = item.task_ids.filter((taskId) => !localTaskIds.includes(taskId));
      let changed = 0;
      if (localTaskIds.length) {
        changed += await updateLocalGenerationTasks(localTaskIds, "RESUME");
      }
      if (cloudTaskIds.length) {
        changed += await updateGenerationQueueItem(user.uid, cloudTaskIds, "RESUME");
      }
      setNotice(changed
        ? "这一章已继续生成，Mac 会自动接着处理。"
        : "这一章的状态已经更新，不需要重复操作。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "继续生成没有保存成功。");
    } finally {
      setDirectoryActionBusy("");
    }
  }

  async function prioritizeChapterGeneration(item: GenerationQueueItem) {
    if (!user) return;
    const activeItems = generationQueue.filter((entry) => (
      entry.status !== "COMPLETED" && entry.status !== "REMOVED"
    ));
    const generating = activeItems.filter((entry) => entry.status === "GENERATING");
    const movable = activeItems.filter((entry) => (
      (entry.status === "QUEUED" || entry.status === "PAUSED")
      && entry.queue_id !== item.queue_id
    ));
    setDirectoryActionBusy(item.queue_id);
    try {
      const ordered = [...generating, item, ...movable];
      const localTaskIds = ordered.flatMap((entry) => entry.task_ids.filter((taskId) => (
        generationTasks.find((task) => task.task_id === taskId)?.execution_mode === "LOCAL"
      )));
      const cloudItems = ordered
        .map((entry) => ({
          ...entry,
          task_ids: entry.task_ids.filter((taskId) => !localTaskIds.includes(taskId)),
        }))
        .filter((entry) => entry.task_ids.length > 0);
      if (localTaskIds.length) await reorderLocalGenerationTasks(localTaskIds);
      if (cloudItems.length) await reorderGenerationQueue(user.uid, cloudItems);
      setNotice(generating.length
        ? "已置顶；当前章节完成后，会优先生成这一章。"
        : "已置顶；这一章会优先生成。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "置顶顺序没有保存成功。");
    } finally {
      setDirectoryActionBusy("");
    }
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

  const cloudOnlyBooks = visibleCloudBooks.filter(
    (cloudBook) => !books.some((book) => book.book_id === cloudBook.book_id),
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => setSelectedBookId("")} aria-label="返回书架">
          <img
            className="brand-seal"
            src={`${import.meta.env.BASE_URL}app-icon-192.png`}
            alt=""
          />
          <span><b>米兰读书</b><small>边听，边读，记住每一处停留</small></span>
        </button>
        <div className="topbar-actions">
          <span className={`sync-state ${user && !localFallbackMode ? "is-online" : ""}`}><i /> {localFallbackMode ? "免费额度保护中" : user ? "云同步已开启" : "本机书架"}</span>
          {firebaseConfigured && !user && <button className="quiet-button header-button mobile-login-button" onClick={() => void logIn()}>登录同步</button>}
          {user && canAdd && !activeMac && <button className="quiet-button header-button" onClick={() => void connectMacAutomatically()}>连接这台 Mac</button>}
          {user && canAdd && activeMac && <button className="quiet-button header-button is-connected" onClick={() => setShowDisconnectMac(true)}>Mac 已连接</button>}
          {canAdd && <button className="quiet-button header-button" onClick={() => setShowVoice(true)}>{voiceStatus?.confirmed ? "声音已设置" : "我的声音"}</button>}
          {user && <button className="quiet-button header-button queue-header-button" onClick={() => setShowGenerationQueue(true)}>待生成 {queuedChapterCount || ""}</button>}
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
          {user && (
            <button className="shelf-queue-button" onClick={() => setShowGenerationQueue(true)}>
              选择待生成章节
            </button>
          )}
          {(user || E2E_PLAYER_MODE) && (
            <button className="shelf-audio-button" onClick={() => setShowAudioManager(true)}>
              管理已生成音频
            </button>
          )}
          {hiddenBooks.length > 0 && (
            <button className="shelf-hidden-button" onClick={() => setShowHiddenBooks(true)}>
              已隐藏书籍 {hiddenBooks.length}
            </button>
          )}
          <section className="book-grid" aria-label="书架">
            {visibleBooks.map((book, index) => {
              const cloudBook = cloudSummaryFor(book, cloudBooks);
              const target: BookRemovalTarget = {
                book_id: book.book_id,
                title: book.title,
                author: book.author,
              };
              return (
                <article className="book-card-shell" key={book.book_id}>
                  <button
                    className={`book-card book-card--tone-${index % 4}`}
                    onClick={() => setSelectedBookId(book.book_id)}
                  >
                    <span className="book-spine" />
                    <span className="book-format">{book.source_format}</span>
                    <span className="book-title">{book.title}</span>
                    <span className="book-author">{book.author || "作者未注明"}</span>
                    <span className="book-meta">{book.chapters.length} 章 · {cloudBook ? (book.publication_mode === "LOCAL_ONLY" ? "账号私有" : "已同步") : book.publication_mode === "LOCAL_ONLY" ? "等待私有同步" : "等待同步"}</span>
                  </button>
                  <button
                    className="book-menu-trigger"
                    aria-label={`管理《${book.title}》`}
                    aria-expanded={bookMenuId === book.book_id}
                    onClick={() => setBookMenuId((current) => current === book.book_id ? "" : book.book_id)}
                  >
                    ⋯
                  </button>
                  {bookMenuId === book.book_id && (
                    <div className="book-menu" role="menu" aria-label={`《${book.title}》操作`}>
                      <button role="menuitem" onClick={() => void hideFromShelf(target)}>从这台设备的书架隐藏</button>
                      {user && cloudBook && book.book_id !== DEMO_BOOK_ID && (
                        <button className="is-danger" role="menuitem" onClick={() => beginPermanentDeletion(target)}>
                          永久删除这本书
                        </button>
                      )}
                      <button role="menuitem" onClick={() => setBookMenuId("")}>关闭</button>
                    </div>
                  )}
                </article>
              );
            })}
            {cloudOnlyBooks.map((book, index) => {
              const target: BookRemovalTarget = {
                book_id: book.book_id,
                title: book.title,
                author: book.author,
              };
              return (
                <article className="book-card-shell" key={book.book_id}>
                  <button
                    className={`book-card book-card--cloud book-card--tone-${(visibleBooks.length + index) % 4}`}
                    onClick={() => void openRemoteBook(book)}
                  >
                    <span className="book-spine" />
                    <span className="book-format">{book.source_format}</span>
                    <span className="book-title">{book.title}</span>
                    <span className="book-author">{book.author || "作者未注明"}</span>
                    <span className="book-meta">{book.chapter_count} 章 · {book.publication_mode === "LOCAL_ONLY" ? (book.text_status === "READY" ? "账号私有" : "等待生成") : "云端书架"}</span>
                  </button>
                  <button
                    className="book-menu-trigger"
                    aria-label={`管理《${book.title}》`}
                    aria-expanded={bookMenuId === book.book_id}
                    onClick={() => setBookMenuId((current) => current === book.book_id ? "" : book.book_id)}
                  >
                    ⋯
                  </button>
                  {bookMenuId === book.book_id && (
                    <div className="book-menu" role="menu" aria-label={`《${book.title}》操作`}>
                      <button role="menuitem" onClick={() => void hideFromShelf(target)}>从这台设备的书架隐藏</button>
                      <button className="is-danger" role="menuitem" onClick={() => beginPermanentDeletion(target)}>
                        永久删除这本书
                      </button>
                      <button role="menuitem" onClick={() => setBookMenuId("")}>关闭</button>
                    </div>
                  )}
                </article>
              );
            })}
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
            {user ? (
              <button
                className="generate-audio-button"
                onClick={() => setShowGenerationQueue(true)}
                disabled={busy}
              >
                选择要生成的章节
              </button>
            ) : (
              <p className="mobile-generation-hint">登录后可以选择章节，并安排生成顺序。</p>
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
              {selectedBook.chapters.map((chapter) => {
                const status = chapterDirectoryStatus(
                  selectedBook,
                  chapter,
                  audioChunks,
                  generationQueue,
                );
                const queueItem = generationQueue.find((item) => (
                  item.book_id === selectedBook.book_id
                  && item.chapter_id === chapter.chapter_id
                ));
                const canPrioritize = Boolean(
                  user
                  && queueItem
                  && (queueItem.status === "QUEUED" || queueItem.status === "PAUSED"),
                );
                const alreadyPrioritized = (
                  priorityGenerationItems[0]?.queue_id === queueItem?.queue_id
                );
                const actionBusy = directoryActionBusy === queueItem?.queue_id;
                const statusDescription = [
                  status.playable_label,
                  status.generation_label || (!status.playable ? "尚未生成" : ""),
                ].filter(Boolean).join("，");
                return (
                  <div
                    key={chapter.chapter_id}
                    className={[
                      "toc-chapter-item",
                      chapter.chapter_id === selectedChapter?.chapter_id ? "is-active" : "",
                    ].filter(Boolean).join(" ")}
                  >
                    <button
                      className="toc-chapter-open"
                      onClick={() => openChapter(chapter)}
                      aria-label={`${chapter.title}，${statusDescription}`}
                    >
                      <span className="toc-chapter-number">
                        {String(chapter.order + 1).padStart(2, "0")}
                      </span>
                      <span className="toc-chapter-copy">
                        <b>{chapter.title}</b>
                        <small>{chapterPreview(chapter)}</small>
                        {status.progress_percent !== null
                          && status.progress_percent > 0
                          && status.progress_percent < 100
                          && (
                            <span
                              className={`toc-progress toc-progress--${status.generation_tone}`}
                              role="progressbar"
                              aria-label={`${chapter.title}生成进度`}
                              aria-valuemin={0}
                              aria-valuemax={100}
                              aria-valuenow={status.progress_percent}
                            >
                              <span style={{ width: `${status.progress_percent}%` }} />
                            </span>
                          )}
                      </span>
                      <span className="toc-chapter-status" aria-hidden="true">
                        {status.playable && (
                          <span className="toc-status-badge toc-status-badge--playable">
                            {status.playable_label}
                          </span>
                        )}
                        {status.generation_label && status.generation_tone !== "paused" && (
                          <span className={`toc-status-badge toc-status-badge--${status.generation_tone}`}>
                            {status.generation_label}
                          </span>
                        )}
                        {!status.playable && !status.generation_label && (
                          <span className="toc-status-badge toc-status-badge--none">尚未生成</span>
                        )}
                      </span>
                    </button>
                    {user && queueItem && (
                      queueItem.status === "PAUSED" || canPrioritize
                    ) && (
                      <div className="toc-chapter-actions">
                        {queueItem.status === "PAUSED" && (
                          <button
                            className="toc-action-button toc-action-button--resume"
                            disabled={actionBusy}
                            onClick={() => void resumeChapterGeneration(queueItem)}
                            aria-label={`继续生成${chapter.title}`}
                          >
                            {actionBusy ? "处理中…" : "继续生成"}
                          </button>
                        )}
                        {canPrioritize && (
                          <button
                            className="toc-action-button"
                            disabled={actionBusy || alreadyPrioritized}
                            onClick={() => void prioritizeChapterGeneration(queueItem)}
                            aria-label={`${alreadyPrioritized ? "已经置顶" : "将"}${chapter.title}${alreadyPrioritized ? "" : "置顶"}`}
                          >
                            {alreadyPrioritized ? "已置顶" : "置顶"}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
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

      {showHiddenBooks && (
        <div className="modal-backdrop" role="presentation">
          <section className="import-modal hidden-books-modal" role="dialog" aria-modal="true" aria-labelledby="hidden-books-title">
            <span className="modal-kicker">只影响这台设备</span>
            <h2 id="hidden-books-title">已隐藏书籍</h2>
            <p>隐藏不会删除正文、音频或阅读位置，随时可以恢复到书架。</p>
            <div className="hidden-book-list">
              {hiddenBooks.map((book) => (
                <article key={book.book_id}>
                  <span><b>{book.title}</b><small>{book.author || "作者未注明"}</small></span>
                  <button className="quiet-button" onClick={() => void restoreHiddenBook(book)}>恢复</button>
                </article>
              ))}
            </div>
            <div className="modal-actions">
              <button className="primary-button" onClick={() => setShowHiddenBooks(false)}>完成</button>
            </div>
          </section>
        </div>
      )}

      {deletionTarget && (
        <div className="modal-backdrop book-delete-backdrop" role="presentation">
          <section className="import-modal book-delete-modal" role="dialog" aria-modal="true" aria-labelledby="book-delete-title">
            <span className="modal-kicker">永久删除 · 第 {deletionStep} 步 / 2</span>
            <h2 id="book-delete-title">删除《{deletionTarget.title}》？</h2>
            {deletionStep === 1 ? (
              <>
                <p>这会停止该书正在运行和等待中的生成任务，并删除账号中的正文、阅读位置、书签和全部音频。Mac 中保存的这本书副本也会清理。</p>
                <div className="delete-impact-list" aria-label="将删除的内容">
                  <span>书籍正文与章节</span>
                  <span>阅读位置与书签</span>
                  <span>已生成音频与待生成任务</span>
                  <span>Mac 中的本机书籍副本</span>
                </div>
                <p className="delete-warning">此操作无法撤销；你的声音样本和其他书籍不会受到影响。</p>
                <div className="modal-actions">
                  <button className="quiet-button" onClick={() => setDeletionTarget(null)}>取消</button>
                  <button className="danger-button" onClick={() => setDeletionStep(2)}>继续核对</button>
                </div>
              </>
            ) : (
              <>
                <p>为防止误触，请在下方输入“永久删除”。</p>
                <input
                  className="delete-confirmation-input"
                  value={deletionConfirmation}
                  onChange={(event) => setDeletionConfirmation(event.target.value)}
                  placeholder="永久删除"
                  autoComplete="off"
                  autoFocus
                />
                <div className="modal-actions">
                  <button className="quiet-button" onClick={() => setDeletionStep(1)} disabled={busy}>返回</button>
                  <button
                    className="danger-button"
                    onClick={() => void permanentlyDeleteBook()}
                    disabled={busy || deletionConfirmation !== "永久删除"}
                  >
                    {busy ? "正在安全删除..." : "确认永久删除"}
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
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
          books={visibleBooks}
          cloudBooks={visibleCloudBooks}
          initialChunks={E2E_PLAYER_MODE ? visibleBooks.flatMap(e2eAudioChunks) : undefined}
          onClose={() => setShowAudioManager(false)}
          onNotice={setNotice}
        />
      )}

      {showGenerationQueue && user && (
        <GenerationQueue
          ownerUid={user.uid}
          books={visibleBooks}
          tasks={generationTasks}
          voiceVersion={effectiveVoiceVersion}
          initialBookId={selectedBook?.book_id}
          initialChapterId={selectedChapter?.chapter_id}
          macOnline={macOnline}
          localMode={localFallbackMode}
          localAudioChunks={allLocalAudioChunks}
          onClose={() => setShowGenerationQueue(false)}
          onNotice={setNotice}
          onOpenAudioManager={() => {
            setShowGenerationQueue(false);
            setShowAudioManager(true);
          }}
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
