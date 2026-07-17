import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { demoBook } from "./demo";

const testState = vi.hoisted(() => ({
  firebaseConfigured: false,
  user: null as null | { uid: string; photoURL: string | null },
  workerLinks: [] as Array<Record<string, unknown>>,
}));

const pairingMocks = vi.hoisted(() => ({
  revoke: vi.fn(async () => undefined),
}));

vi.mock("./firebase", () => ({
  firebaseIsConfigured: () => testState.firebaseConfigured,
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
  watchMacAgents: vi.fn((_ownerUid: string, listener: (links: unknown[]) => void) => {
    listener(testState.workerLinks);
    return () => undefined;
  }),
}));

vi.mock("./cloud", () => ({
  loadCloudProgress: vi.fn(async () => null),
  saveProgressOptimistically: vi.fn(),
  syncBookMetadata: vi.fn(async () => undefined),
  watchCloudBooks: vi.fn(() => () => undefined),
}));

vi.mock("./device", () => ({ registerCurrentDevice: vi.fn(async () => undefined) }));

vi.mock("./storage", () => ({
  loadBooks: vi.fn(async () => [demoBook]),
  loadCachedCloudBooks: vi.fn(async () => []),
  loadPendingProgress: vi.fn(async () => []),
  loadProgress: vi.fn(async () => undefined),
  bookMetadataNeedsSync: vi.fn(async () => false),
  cacheCloudBooks: vi.fn(async () => undefined),
  markBookMetadataSynced: vi.fn(async () => undefined),
  saveBook: vi.fn(async () => undefined),
  saveProgress: vi.fn(async () => undefined),
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
    pairingMocks.revoke.mockClear();
    setPlatform(1280, "MacIntel");
  });

  it("Mac 桌面显示添加书籍按钮和真实章节正文", async () => {
    render(<App />);
    expect(await screen.findByRole("button", { name: /添加书籍/ })).toBeInTheDocument();
    expect(await screen.findByText("山窗小札")).toBeInTheDocument();
    expect(screen.getAllByText("第一章 清晨")).toHaveLength(2);
    expect(screen.getAllByText(/窗纸先有了温度/)).toHaveLength(2);
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
});
