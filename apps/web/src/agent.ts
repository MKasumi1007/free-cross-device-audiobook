import { parsedBookSchema, type ParsedBook } from "@audiobook/contracts";

import type { AudioChunk, GenerationTaskSummary } from "./cloud";

export const AGENT_BASE_URL = "http://127.0.0.1:17832";

interface AgentSession {
  csrf_token: string;
}

export interface PairingStart {
  code: string;
  expires_in: number;
}

export interface VoiceStatus {
  configured: boolean;
  voice_version?: string;
  duration_seconds?: number;
  confirmed?: boolean;
  preview_available?: boolean;
  preview: {
    state: "IDLE" | "GENERATING" | "READY" | "FAILED";
    error: string;
    model_loaded: boolean;
  };
}

export interface AgentDiagnosticItem {
  key: string;
  label: string;
  status: "ok" | "warning" | "failed";
  detail: string;
  suggestion: string;
  repair_action: string;
}

export interface AgentDiagnostics {
  schema_version: number;
  checked_at: string;
  agent_version: string;
  agent_port: number;
  data_root: string;
  log_path: string;
  worker: {
    state: string;
    error: string;
    model_loaded: boolean;
    cloud_backoff_seconds?: number;
    local_pending_sync?: number;
  };
  recent_error: null | {
    timestamp: string;
    operation: string;
    error_code: string;
    message: string;
  };
  items: AgentDiagnosticItem[];
}

export interface LocalGenerationSelection {
  book_id: string;
  chapter_ids: string[];
  task_ids?: string[];
}

export interface LocalGenerationResult {
  chapters: number;
  created: number;
  resumed: number;
  unchanged: number;
}

export interface LocalGenerationStatus {
  schema_version: number;
  tasks: GenerationTaskSummary[];
  audio_chunks: AudioChunk[];
  pending_sync: number;
  worker: AgentDiagnostics["worker"];
}

interface AgentErrorPayload {
  code?: string;
  error?: string;
}

export class AgentRequestError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message);
  }
}

async function responseError(response: Response): Promise<AgentRequestError> {
  try {
    const payload = (await response.json()) as AgentErrorPayload;
    return new AgentRequestError(
      payload.code || `AGENT_HTTP_${response.status}`,
      payload.error || `Mac Agent 返回错误 ${response.status}`,
    );
  } catch {
    return new AgentRequestError(`AGENT_HTTP_${response.status}`, `Mac Agent 返回错误 ${response.status}`);
  }
}

export async function chooseBookOnMac(rightsConfirmed: boolean): Promise<ParsedBook | null> {
  const csrfToken = await getAgentSession();
  const response = await fetch(`${AGENT_BASE_URL}/v1/books/choose`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Audiobook-CSRF": csrfToken,
    },
    body: JSON.stringify({ import_as_copy: false, rights_confirmed: rightsConfirmed }),
  });
  if (response.status === 204) {
    return null;
  }
  if (!response.ok) {
    throw await responseError(response);
  }
  return parsedBookSchema.parse(await response.json());
}

async function getAgentSession(): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${AGENT_BASE_URL}/v1/session`);
  } catch {
    throw new AgentRequestError("AGENT_UNREACHABLE", "没有找到 Mac Agent。请先双击安装器，或打开“系统状态”。");
  }
  if (!response.ok) {
    throw await responseError(response);
  }
  return ((await response.json()) as AgentSession).csrf_token;
}

export async function startPairingOnMac(): Promise<PairingStart> {
  const csrfToken = await getAgentSession();
  const response = await fetch(`${AGENT_BASE_URL}/v1/pairing/start`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Audiobook-CSRF": csrfToken,
    },
    body: "{}",
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return await response.json() as PairingStart;
}

export async function getVoiceStatus(): Promise<VoiceStatus> {
  const response = await fetch(`${AGENT_BASE_URL}/v1/voice/status`);
  if (!response.ok) throw await responseError(response);
  return await response.json() as VoiceStatus;
}

async function postVoice(path: string, body: object): Promise<Response> {
  const csrfToken = await getAgentSession();
  return fetch(`${AGENT_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Audiobook-CSRF": csrfToken,
    },
    body: JSON.stringify(body),
  });
}

export async function chooseVoiceOnMac(transcript: string): Promise<VoiceStatus | null> {
  const response = await postVoice("/v1/voice/choose", { transcript });
  if (response.status === 204) return null;
  if (!response.ok) throw await responseError(response);
  return getVoiceStatus();
}

export async function startVoicePreview(): Promise<VoiceStatus> {
  const response = await postVoice("/v1/voice/preview", {});
  if (!response.ok) throw await responseError(response);
  return getVoiceStatus();
}

export async function confirmVoice(voiceVersion: string): Promise<VoiceStatus> {
  const response = await postVoice("/v1/voice/confirm", { voice_version: voiceVersion });
  if (!response.ok) throw await responseError(response);
  return getVoiceStatus();
}

export async function loadVoicePreview(voiceVersion: string): Promise<Blob> {
  const response = await fetch(
    `${AGENT_BASE_URL}/v1/voice/preview.m4a?v=${encodeURIComponent(voiceVersion)}`,
    { cache: "no-store" },
  );
  if (!response.ok) throw await responseError(response);
  return response.blob();
}

export async function getAgentDiagnostics(): Promise<AgentDiagnostics> {
  let response: Response;
  try {
    response = await fetch(`${AGENT_BASE_URL}/v1/diagnostics`, { cache: "no-store" });
  } catch {
    throw new AgentRequestError("AGENT_UNREACHABLE", "Mac Agent 端口 17832 无法访问。请先安装或启动后台服务。");
  }
  if (!response.ok) throw await responseError(response);
  return await response.json() as AgentDiagnostics;
}

export async function startAgentRepair(action: string): Promise<void> {
  const response = await postVoice("/v1/diagnostics/repair", { action });
  if (!response.ok) throw await responseError(response);
}

export async function getLocalGenerationStatus(): Promise<LocalGenerationStatus> {
  const response = await fetch(`${AGENT_BASE_URL}/v1/local-generation/status`, {
    cache: "no-store",
  });
  if (!response.ok) throw await responseError(response);
  return await response.json() as LocalGenerationStatus;
}

export async function enqueueLocalGeneration(
  ownerUid: string,
  selections: readonly LocalGenerationSelection[],
  voiceVersion: string,
): Promise<LocalGenerationResult> {
  const response = await postVoice("/v1/local-generation/enqueue", {
    owner_uid: ownerUid,
    selections,
    voice_version: voiceVersion,
  });
  if (!response.ok) throw await responseError(response);
  return await response.json() as LocalGenerationResult;
}

export async function updateLocalGenerationTasks(
  taskIds: readonly string[],
  action: "PAUSE" | "RESUME" | "REMOVE",
): Promise<number> {
  const response = await postVoice("/v1/local-generation/action", {
    task_ids: taskIds,
    action,
  });
  if (!response.ok) throw await responseError(response);
  return Number(((await response.json()) as { changed?: number }).changed || 0);
}

export async function reorderLocalGenerationTasks(taskIds: readonly string[]): Promise<number> {
  const response = await postVoice("/v1/local-generation/reorder", { task_ids: taskIds });
  if (!response.ok) throw await responseError(response);
  return Number(((await response.json()) as { changed?: number }).changed || 0);
}
