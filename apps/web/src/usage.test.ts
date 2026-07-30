import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cloudSyncIsPaused,
  cloudSyncPauseMessage,
  getCloudSyncPause,
  getUsageEstimate,
  pauseCloudSync,
  recordEstimatedUsage,
} from "./usage";
import { classifyFirebaseError } from "./firebase-errors";

describe("free quota safety", () => {
  let values: Map<string, string>;

  beforeEach(() => {
    values = new Map<string, string>();
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

  it("keeps reads available when listener snapshots overestimate billed reads", () => {
    recordEstimatedUsage({ reads: 45_000 });
    expect(getUsageEstimate().reads).toBe(45_000);
    expect(cloudSyncIsPaused()).toBe(false);
  });

  it("discards legacy pause markers created by the old read overcount", () => {
    values.set("audiobook-firestore-sync-pause", JSON.stringify({
      date: new Date().toISOString().slice(0, 10),
      reason: "REMOTE_QUOTA",
    }));
    expect(getCloudSyncPause()).toBeNull();
    expect(cloudSyncIsPaused()).toBe(false);
  });

  it("does not relabel a local safety pause as a remote quota failure", () => {
    pauseCloudSync("ESTIMATED_LIMIT");
    const result = classifyFirebaseError({
      code: "resource-exhausted-local-safety-pause",
    });
    expect(result.kind).toBe("FREE_QUOTA");
    expect(getCloudSyncPause()?.reason).toBe("ESTIMATED_LIMIT");
  });

  it("records a remote quota pause without suggesting a paid upgrade", () => {
    pauseCloudSync("REMOTE_QUOTA");
    expect(cloudSyncPauseMessage()).toContain("明天会自动恢复");
    expect(cloudSyncPauseMessage()).not.toContain("升级");
  });
});
