import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { demoBook } from "./demo";

const testState = vi.hoisted(() => ({
  firebaseConfigured: false,
  user: null as null | { uid: string; photoURL: string | null },
  workerLinks: [] as Array<Record<string, unknown>>,
  cloudBooks: [] as Array<Record<string, unknown>>,
  generationQueue: [] as Array<Record<string, unknown>>,
}));

const pairingMocks = vi.hoisted(() => ({
  revoke: vi.fn(async () => undefined),
}));

const bookMocks = vi.hoisted(() => ({
  deleteLocal: vi.fn(async () => undefined),
  hide: vi.fn(async () => undefined),
  requestDeletion: vi.fn(async () => ({
    cancelled_tasks: 1,
    queued_audio_chunks: 2,
  })),
}));

const queueMocks = vi.hoisted(() => ({
  reorder: vi.fn(async () => undefined),
  update: vi.fn(async () => 1),
}));

vi.mock("./firebase", () => ({
  firebaseIsConfigured: () => testState.firebaseConfigured,
  checkFirestoreConnection: vi.fn(async () => undefined),
}));

vi.mock("./auth", () => ({
  signInWithGoogle: vi.fn(async () => undefined),
  signOutCurrentUser: vi.fn(async () => undefined),
  watchAuth: vi.fn((listener: (user: unknown) => void) => {
    listener(testState.user);
    return () => undefined;
  }),
}));

vi.mock("./pairing", () => ({
  pairMacAgent: vi.fn(async () => "worker-a"),
  revokeMacAgent: pairingMocks.revoke,
  workerIsOnline: vi.fn(() => false),
  watchMacAgents: vi.fn((_ownerUid: string, listener: (links: unknown[]) => void) => {
    listener(testState.workerLinks);
    return () => undefined;
  }),
}));

vi.mock("./agent", () => ({
  chooseBookOnMac: vi.fn(async () => null),
  chooseVoiceOnMac: vi.fn(async () => null),
  confirmVoice: vi.fn(),
  getVoiceStatus: vi.fn(async () => ({
    configured: false,
    preview: { state: "IDLE", error: "", model_loaded: false },
  })),
  getAgentDiagnostics: vi.fn(async () => ({
    schema_version: 1,
    checked_at: "2026-07-18T00:00:00Z",
    agent_version: "0.4.0",
    agent_port: 17832,
    data_root: "/private/application-support",
    log_path: "/private/application-support/logs/diagnostics.jsonl",
    worker: { state: "IDLE", error: "", model_loaded: false },
    recent_error: null,
    items: [],
  })),
  startAgentRepair: vi.fn(async () => undefined),
  loadVoicePreview: vi.fn(async () => new Blob()),
  startPairingOnMac: vi.fn(),
  startVoicePreview: vi.fn(),
}));

vi.mock("./cloud", () => ({
  buildGenerationQueue: vi.fn(() => testState.generationQueue),
  enqueueGenerationChapters: vi.fn(),
  generationTaskIsLive: vi.fn(() => false),
  loadCloudProgress: vi.fn(async () => null),
  loadRemoteBook: vi.fn(async () => demoBook),
  loadVoiceGenerationProfile: vi.fn(async () => null),
  markBookListened: vi.fn(async () => undefined),
  reorderGenerationQueue: queueMocks.reorder,
  requestAudioRepair: vi.fn(async () => undefined),
  requestBookDeletion: bookMocks.requestDeletion,
  saveBookmark: vi.fn(async () => "bookmark-a"),
  saveProgressOptimistically: vi.fn(),
  saveVoiceGenerationProfile: vi.fn(async () => undefined),
  syncBookMetadata: vi.fn(async () => undefined),
  updateGenerationQueueItem: queueMocks.update,
  watchAudioChunks: vi.fn(() => () => undefined),
  watchBookmarks: vi.fn(() => () => undefined),
  watchCloudBooks: vi.fn((_ownerUid: string, listener: (books: unknown[]) => void) => {
    listener(testState.cloudBooks);
    return () => undefined;
  }),
  watchGenerationTasks: vi.fn((_ownerUid: string, listener: (tasks: unknown[]) => void) => {
    listener([]);
    return () => undefined;
  }),
}));

vi.mock("./device", () => ({ registerCurrentDevice: vi.fn(async () => undefined) }));

vi.mock("./storage", () => ({
  loadBooks: vi.fn(async () => [demoBook]),
  loadCachedCloudBooks: vi.fn(async () => []),
  loadHiddenBooks: vi.fn(async () => []),
  loadPendingProgress: vi.fn(async () => []),
  loadProgress: vi.fn(async () => undefined),
  bookMetadataNeedsSync: vi.fn(async () => false),
  cacheCloudBooks: vi.fn(async () => undefined),
  deleteLocalBook: bookMocks.deleteLocal,
  hideBook: bookMocks.hide,
  markBookMetadataSynced: vi.fn(async () => undefined),
  saveBook: vi.fn(async () => undefined),
  saveProgress: vi.fn(async () => undefined),
  unhideBook: vi.fn(async () => undefined),
}));

function setPlatform(width: number, platform: string, touchPoints = 0) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  Object.defineProperty(navigator, "platform", { configurable: true, value: platform });
  Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: touchPoints });
}

describe("书架响应式入口", () => {
  beforeEach(() => {
    testState.firebaseConfigured = false;
    testState.user = null;
    testState.workerLinks = [];
    testState.cloudBooks = [];
    testState.generationQueue = [];
    pairingMocks.revoke.mockClear();
    bookMocks.deleteLocal.mockClear();
    bookMocks.hide.mockClear();
    bookMocks.requestDeletion.mockClear();
    queueMocks.reorder.mockClear();
    queueMocks.update.mockClear();
    setPlatform(1280, "MacIntel");
  });

  it("Mac 桌面显示添加书籍按钮和真实章节正文", async () => {
    render(<App />);
    expect(screen.getByText("米兰读书")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /添加书籍/ })).toBeInTheDocument();
    expect(await screen.findByText("山窗小札")).toBeInTheDocument();
    expect(screen.getAllByText("第一章 清晨")).toHaveLength(2);
    expect(screen.getAllByText(/窗纸先有了温度/)).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "我的声音" }));
    expect(screen.getByRole("heading", { name: "设置我的声音" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("在这里填写录音中说的全部文字")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "系统状态" }));
    expect(await screen.findByRole("heading", { name: "系统状态" })).toBeInTheDocument();
    expect(await screen.findByText("Agent 版本")).toBeInTheDocument();
  });

  it("手机视口不显示添加按钮，并提示去 Mac 添加", async () => {
    setPlatform(390, "iPhone", 5);
    render(<App />);
    await screen.findByText("山窗小札");
    fireEvent.click(screen.getByLabelText("返回书架"));
    await waitFor(() => expect(screen.getByText("请在 Mac 上添加新书")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /添加书籍/ })).not.toBeInTheDocument();
  });

  it("Firebase 配置完成后显示容易理解的登录同步按钮", async () => {
    testState.firebaseConfigured = true;
    render(<App />);
    expect(await screen.findByRole("button", { name: "登录同步" })).toBeInTheDocument();
  });

  it("手机端保留登录入口，未登录时说明登录后可以安排章节", async () => {
    setPlatform(390, "iPhone", 5);
    testState.firebaseConfigured = true;
    render(<App />);

    expect(await screen.findByRole("button", { name: "登录同步" })).toHaveClass(
      "mobile-login-button",
    );
    expect(screen.queryByRole("button", { name: "选择要生成的章节" })).not.toBeInTheDocument();
    expect(screen.getByText("登录后可以选择章节，并安排生成顺序。")).toBeInTheDocument();
  });

  it("手机登录后可以打开章节待生成列表", async () => {
    setPlatform(390, "iPhone", 5);
    testState.firebaseConfigured = true;
    testState.user = { uid: "owner-a", photoURL: null };
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "选择要生成的章节" }));
    expect(screen.getByRole("heading", { name: "待生成列表" })).toBeInTheDocument();
    expect(screen.getByText("第一步")).toBeInTheDocument();
    expect(screen.getByText("第二步")).toBeInTheDocument();
  });

  it("可在目录中继续暂停章节并把它置顶", async () => {
    testState.firebaseConfigured = true;
    testState.user = { uid: "owner-a", photoURL: null };
    const queueItem = (chapterId: string, chapterTitle: string, status: string, priority: number) => ({
      queue_id: `${demoBook.book_id}:${chapterId}`,
      book_id: demoBook.book_id,
      chapter_id: chapterId,
      book_title: demoBook.title,
      chapter_title: chapterTitle,
      status,
      priority,
      task_ids: [`task-${chapterId}`],
      total_chunks: 2,
      ready_chunks: 0,
      pending_chunks: 2,
      estimated_seconds: 100,
      progress_percent: 0,
      progress_stage: "",
      current_task_id: "",
      current_piece: 0,
      current_piece_total: 0,
      generated_audio_seconds: 0,
      elapsed_seconds: 0,
      eta_seconds: null,
      chapter_eta_seconds: null,
      historical_pause: false,
    });
    const secondChapter = demoBook.chapters[1]!;
    const firstChapter = demoBook.chapters[0]!;
    const queued = queueItem(secondChapter.chapter_id, secondChapter.title, "QUEUED", 1_000);
    const paused = queueItem(firstChapter.chapter_id, firstChapter.title, "PAUSED", 900);
    testState.generationQueue = [queued, paused];
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: `继续生成${firstChapter.title}` }));
    await waitFor(() => expect(queueMocks.update).toHaveBeenCalledWith(
      "owner-a",
      paused.task_ids,
      "RESUME",
    ));

    fireEvent.click(screen.getByRole("button", { name: `将${firstChapter.title}置顶` }));
    await waitFor(() => expect(queueMocks.reorder).toHaveBeenCalledWith(
      "owner-a",
      [paused, queued],
    ));
  });

  it("已配对时显示连接状态，并可确认撤销 Mac 权限", async () => {
    testState.firebaseConfigured = true;
    testState.user = { uid: "owner-a", photoURL: null };
    testState.workerLinks = [{
      worker_uid: "worker-a",
      owner_uid: "owner-a",
      worker_type: "MAC_AGENT",
      scopes: ["generation"],
      revoked_at: null,
      last_seen_at: null,
    }];
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Mac 已连接" }));
    expect(screen.getByRole("heading", { name: "断开这台 Mac？" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认断开" }));
    await waitFor(() => expect(pairingMocks.revoke).toHaveBeenCalledWith("owner-a", "worker-a"));
  });

  it("可从书卡菜单隐藏一本书，并说明可以恢复", async () => {
    render(<App />);
    fireEvent.click(await screen.findByLabelText("返回书架"));
    fireEvent.click(screen.getByRole("button", { name: "管理《山窗小札》" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "从这台设备的书架隐藏" }));

    await waitFor(() => expect(bookMocks.hide).toHaveBeenCalledWith({
      book_id: demoBook.book_id,
      title: demoBook.title,
      author: demoBook.author,
    }));
    expect(screen.getByRole("status")).toHaveTextContent("已从这台设备的书架隐藏");
    expect(screen.getByRole("button", { name: /已隐藏书籍 1/ })).toBeInTheDocument();
  });

  it("永久删除必须经过两步确认并输入指定文字", async () => {
    testState.firebaseConfigured = true;
    testState.user = { uid: "owner-a", photoURL: null };
    testState.cloudBooks = [{
      book_id: "cloud-delete-book",
      title: "云端待删书",
      author: "测试作者",
      source_format: "EPUB",
      source_sha256: "a".repeat(64),
      publication_mode: "LOCAL_ONLY",
      chapter_count: 2,
      segment_count: 20,
      text_status: "READY",
    }];
    render(<App />);
    fireEvent.click(await screen.findByLabelText("返回书架"));
    fireEvent.click(await screen.findByRole("button", { name: "管理《云端待删书》" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "永久删除这本书" }));
    expect(screen.getByText("永久删除 · 第 1 步 / 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "继续核对" }));
    const confirmation = screen.getByPlaceholderText("永久删除");
    fireEvent.change(confirmation, { target: { value: "永久删除" } });
    fireEvent.click(screen.getByRole("button", { name: "确认永久删除" }));

    await waitFor(() => expect(bookMocks.requestDeletion).toHaveBeenCalledWith(
      "owner-a",
      "cloud-delete-book",
    ));
    expect(bookMocks.deleteLocal).toHaveBeenCalledWith("cloud-delete-book", "owner-a");
  });
});
