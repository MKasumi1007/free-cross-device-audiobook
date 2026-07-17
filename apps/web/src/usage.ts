const KEY = "audiobook-firestore-usage-estimate";

export interface DailyUsageEstimate {
  date: string;
  reads: number;
  writes: number;
  deletes: number;
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
  return next;
}
