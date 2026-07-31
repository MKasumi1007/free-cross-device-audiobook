import { cloudSyncPauseMessage, pauseCloudSync } from "./usage";

export type SyncErrorKind = "FREE_QUOTA" | "OFFLINE" | "AUTH" | "UNKNOWN";

export interface SyncError {
  kind: SyncErrorKind;
  message: string;
}

export function classifyFirebaseError(error: unknown): SyncError {
  const code = typeof error === "object" && error && "code" in error ? String(error.code) : "";
  if (code.includes("resource-exhausted-local-safety-pause")) {
    return { kind: "FREE_QUOTA", message: cloudSyncPauseMessage() };
  }
  if (code.includes("resource-exhausted") || code.includes("quota-exceeded")) {
    pauseCloudSync("REMOTE_QUOTA");
    return {
      kind: "FREE_QUOTA",
      message: "今日免费云同步额度已暂停；这台 Mac 仍可本地生成和播放，明天会自动恢复并同步。",
    };
  }
  if (code.includes("unavailable") || code.includes("network-request-failed") || code.includes("deadline-exceeded")) {
    return { kind: "OFFLINE", message: "网络暂时不可用，进度已保存在本机，恢复后会再同步。" };
  }
  if (code.includes("unauthenticated") || code.includes("permission-denied")) {
    return { kind: "AUTH", message: "登录或设备授权已失效，请重新登录。" };
  }
  return { kind: "UNKNOWN", message: "云同步暂时失败，本机数据没有丢失。" };
}
