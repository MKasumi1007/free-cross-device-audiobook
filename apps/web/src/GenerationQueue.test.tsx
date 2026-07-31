import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { demoBook } from "./demo";
import { GenerationQueue } from "./GenerationQueue";


const mocks = vi.hoisted(() => ({
  cloudEnqueue: vi.fn(),
  localEnqueue: vi.fn(),
}));

vi.mock("./agent", () => ({
  enqueueLocalGeneration: mocks.localEnqueue,
  reorderLocalGenerationTasks: vi.fn(async () => 0),
  updateLocalGenerationTasks: vi.fn(async () => 0),
}));

vi.mock("./cloud", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./cloud")>();
  return {
    ...actual,
    enqueueGenerationChapters: mocks.cloudEnqueue,
    loadAudioInventory: vi.fn(async () => []),
  };
});

function renderQueue(localMode: boolean) {
  return render(
    <GenerationQueue
      ownerUid="owner-1"
      books={[demoBook]}
      tasks={[]}
      voiceVersion="voice-1"
      macOnline
      localMode={localMode}
      onClose={() => undefined}
      onNotice={() => undefined}
      onOpenAudioManager={() => undefined}
    />,
  );
}

describe("free quota local generation", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    });
    mocks.cloudEnqueue.mockReset();
    mocks.localEnqueue.mockReset();
    mocks.localEnqueue.mockResolvedValue({
      chapters: 1,
      created: 1,
      resumed: 0,
      unchanged: 0,
    });
  });

  it("sends selected chapters directly to the Mac while protection is active", async () => {
    renderQueue(true);
    fireEvent.click(screen.getAllByRole("checkbox")[0]!);
    fireEvent.click(screen.getByRole("button", { name: "交给这台 Mac 生成" }));

    await waitFor(() => expect(mocks.localEnqueue).toHaveBeenCalledWith(
      "owner-1",
      [{ book_id: demoBook.book_id, chapter_ids: [demoBook.chapters[0]!.chapter_id] }],
      "voice-1",
    ));
    expect(mocks.cloudEnqueue).not.toHaveBeenCalled();
  });

  it("falls back to the Mac immediately when Firestore reports quota exhaustion", async () => {
    mocks.cloudEnqueue.mockRejectedValue({ code: "resource-exhausted" });
    renderQueue(false);
    fireEvent.click(screen.getAllByRole("checkbox")[0]!);
    fireEvent.click(screen.getByRole("button", { name: "加入待生成列表" }));

    await waitFor(() => expect(mocks.localEnqueue).toHaveBeenCalledTimes(1));
  });
});
