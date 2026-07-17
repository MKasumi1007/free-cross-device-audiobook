import { describe, expect, it } from "vitest";

import { workerIsOnline, type WorkerLink } from "./pairing";

function link(lastSeen: number, revokedAt: unknown = null): WorkerLink {
  return {
    worker_uid: "worker-a",
    owner_uid: "owner-a",
    worker_type: "MAC_AGENT",
    scopes: ["generation"],
    revoked_at: revokedAt,
    last_seen_at: { toMillis: () => lastSeen },
  };
}

describe("workerIsOnline", () => {
  it("accepts a recent heartbeat and rejects stale or revoked links", () => {
    const now = Date.UTC(2026, 6, 17, 12, 0, 0);
    expect(workerIsOnline(link(now - 9 * 60_000), now)).toBe(true);
    expect(workerIsOnline(link(now - 11 * 60_000), now)).toBe(false);
    expect(workerIsOnline(link(now, { toMillis: () => now }), now)).toBe(false);
    expect(workerIsOnline(undefined, now)).toBe(false);
  });
});
