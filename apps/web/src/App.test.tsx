import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { demoBook } from "./demo";

vi.mock("./storage", () => ({
  loadBooks: vi.fn(async () => [demoBook]),
  loadProgress: vi.fn(async () => undefined),
  saveBook: vi.fn(async () => undefined),
  saveProgress: vi.fn(async () => undefined),
}));

function setPlatform(width: number, platform: string, touchPoints = 0) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  Object.defineProperty(navigator, "platform", { configurable: true, value: platform });
  Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: touchPoints });
}

describe("书架响应式入口", () => {
  beforeEach(() => setPlatform(1280, "MacIntel"));

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
});
