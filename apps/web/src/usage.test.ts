import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cloudSyncIsPaused,
  cloudSyncPauseMessage,
  getUsageEstimate,
  pauseCloudSync,
  recordEstimatedUsage,
} from "./usage";

describe("free quota safety", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    });
  });

  it("pauses before the conservative daily write threshold", () => {
    recordEstimatedUsage({ writes: 18_000 });
    expect(getUsageEstimate().writes).toBe(18_000);
    expect(cloudSyncIsPaused()).toBe(true);
    expect(cloudSyncPauseMessage()).toContain("免费额度");
  });

  it("records a remote quota pause without suggesting a paid upgrade", () => {
    pauseCloudSync("REMOTE_QUOTA");
    expect(cloudSyncPauseMessage()).toContain("明天会自动恢复");
    expect(cloudSyncPauseMessage()).not.toContain("升级");
  });
});
