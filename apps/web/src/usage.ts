const KEY = "audiobook-firestore-usage-estimate";
const PAUSE_KEY = "audiobook-firestore-sync-pause";

// These are deliberately below Firebase's published Spark limits. The counter is
// only a local estimate, so stopping early is safer than risking a paid dependency.
const SAFETY_LIMITS = {
  reads: 45_000,
  writes: 18_000,
  deletes: 18_000,
} as const;

export interface DailyUsageEstimate {
  date: string;
  reads: number;
  writes: number;
  deletes: number;
}

export interface CloudSyncPause {
  date: string;
  reason: "ESTIMATED_LIMIT" | "REMOTE_QUOTA";
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export function getUsageEstimate(): DailyUsageEstimate {
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) || "null") as DailyUsageEstimate | null;
    if (stored?.date === today()) return stored;
  } catch {
    // A damaged estimate is disposable; it is never the source of user data.
  }
  return { date: today(), reads: 0, writes: 0, deletes: 0 };
}

export function recordEstimatedUsage(delta: Partial<Omit<DailyUsageEstimate, "date">>): DailyUsageEstimate {
  const current = getUsageEstimate();
  const next = {
    ...current,
    reads: current.reads + (delta.reads || 0),
    writes: current.writes + (delta.writes || 0),
    deletes: current.deletes + (delta.deletes || 0),
  };
  localStorage.setItem(KEY, JSON.stringify(next));
  if (
    next.reads >= SAFETY_LIMITS.reads
    || next.writes >= SAFETY_LIMITS.writes
    || next.deletes >= SAFETY_LIMITS.deletes
  ) {
    pauseCloudSync("ESTIMATED_LIMIT");
  }
  return next;
}

export function pauseCloudSync(reason: CloudSyncPause["reason"]): void {
  localStorage.setItem(PAUSE_KEY, JSON.stringify({ date: today(), reason } satisfies CloudSyncPause));
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("audiobook-cloud-sync-paused"));
  }
}

export function getCloudSyncPause(): CloudSyncPause | null {
  try {
    const stored = JSON.parse(localStorage.getItem(PAUSE_KEY) || "null") as CloudSyncPause | null;
    if (stored?.date === today()) return stored;
  } catch {
    // A damaged pause marker can be safely discarded.
  }
  if (typeof localStorage.removeItem === "function") localStorage.removeItem(PAUSE_KEY);
  return null;
}

export function cloudSyncIsPaused(): boolean {
  return getCloudSyncPause() !== null;
}

export function cloudSyncPauseMessage(): string {
  const pause = getCloudSyncPause();
  if (!pause) return "";
  return pause.reason === "REMOTE_QUOTA"
    ? "今日免费云同步额度已暂停，本机书架和进度仍可使用，明天会自动恢复。"
    : "为避免超出免费额度，今日云同步已提前暂停，明天会自动恢复。";
}
