import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const fixture = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "fixtures/synthetic-tone.m4a",
);

test.beforeEach(async ({ page }, testInfo) => {
  if (testInfo.project.name === "desktop-chrome") {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "platform", { configurable: true, value: "MacIntel" });
      Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
    });
  }
  await page.route("https://e2e.invalid/**", async (route) => {
    await route.fulfill({
      path: fixture,
      contentType: "audio/mp4",
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store",
      },
    });
  });
  await page.goto("?e2e=player");
  await expect(page.getByRole("heading", { name: "山窗小札" }).first()).toBeVisible();
});

test("plays, navigates, bookmarks, and switches books without mixing state", async ({ page }) => {
  const play = page.getByRole("button", { name: "播放" });
  await expect(play).toBeEnabled();
  await play.click();
  await expect(page.getByRole("button", { name: "暂停" })).toBeVisible();
  await page.getByLabel("播放速度").selectOption("1.5");
  await expect(page.getByLabel("播放速度")).toHaveValue("1.5");
  await page.getByLabel("睡眠定时").selectOption("15");

  await page.getByRole("button", { name: "书签" }).click();
  await expect(page.getByRole("status")).toContainText("登录同步后");

  await page.getByLabel("返回书架").click();
  await page.locator(".book-card").filter({ hasText: "山窗小札 · 第二册" }).click();
  await expect(page.getByRole("heading", { name: "山窗小札 · 第二册" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "播放" })).toBeEnabled();
});

test("hides and restores a book from the shelf", async ({ page }) => {
  await page.goto("./?e2e=player");
  await page.getByLabel("返回书架").click();
  await page.getByRole("button", { name: "管理《山窗小札》" }).click();
  await page.getByRole("menuitem", { name: "从这台设备的书架隐藏" }).click();

  await expect(page.getByRole("button", { name: /已隐藏书籍 1/ })).toBeVisible();
  await page.getByRole("button", { name: /已隐藏书籍 1/ }).click();
  await page.getByRole("button", { name: "恢复" }).click();
  await expect(page.locator(".book-card").filter({ hasText: "山窗小札" }).first()).toBeVisible();
});

test("restores a deliberately selected reading position after reload", async ({ page }) => {
  const text = page.getByText("水在壶中渐响，我把昨日读到的地方重新翻开。", { exact: true });
  await text.click();
  await expect(text).toHaveClass(/reader-segment--selected/);
  await page.reload();
  const restored = page.getByText("水在壶中渐响，我把昨日读到的地方重新翻开。", { exact: true });
  await expect(restored).toHaveClass(/reader-segment--selected/);
});

test("uses the correct responsive controls", async ({ page }, testInfo) => {
  await expect(page.getByLabel("听书播放器")).toBeVisible();
  if (testInfo.project.name === "mobile-chrome") {
    await expect(page.getByRole("button", { name: "选择要生成的章节" })).toHaveCount(0);
    await expect(page.getByText("登录后可以选择章节，并安排生成顺序。")).toBeVisible();
    await page.getByLabel("返回书架").click();
    await expect(page.getByRole("button", { name: "登录同步" })).toBeVisible();
    await expect(page.getByText("请在 Mac 上添加新书")).toBeVisible();
    await expect(page.getByRole("button", { name: /添加书籍/ })).toHaveCount(0);
  } else {
    await page.getByLabel("返回书架").click();
    await expect(page.getByRole("button", { name: /添加书籍/ })).toBeVisible();
  }
});

test("manages audio by chapter with an explicit irreversible confirmation", async ({ page }) => {
  await page.getByRole("button", { name: "管理已生成音频" }).click();
  await expect(page.getByRole("heading", { name: "音频空间" })).toBeVisible();
  await expect(page.getByText("正在占用")).toBeVisible();
  await page.getByRole("button", { name: "删除本章音频" }).first().click();
  await expect(page.getByRole("heading", { name: /删除.*远程音频/ })).toBeVisible();
  await expect(page.getByText(/书籍、正文、书签、进度和你的声音都会保留/)).toBeVisible();
  await page.getByRole("button", { name: "确认删除音频" }).click();
  await expect(page.getByText("删除中").first()).toBeVisible();
});
