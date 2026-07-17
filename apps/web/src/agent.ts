import { parsedBookSchema, type ParsedBook } from "@audiobook/contracts";

const AGENT_BASE_URL = "http://127.0.0.1:17832";

interface AgentSession {
  csrf_token: string;
}

export interface PairingStart {
  code: string;
  expires_in: number;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { error?: string };
    return payload.error || `Mac Agent 返回错误 ${response.status}`;
  } catch {
    return `Mac Agent 返回错误 ${response.status}`;
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
    throw new Error(await errorMessage(response));
  }
  return parsedBookSchema.parse(await response.json());
}

async function getAgentSession(): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${AGENT_BASE_URL}/v1/session`);
  } catch {
    throw new Error("没有找到 Mac Agent。请先打开桌面的“启动听书工具”。");
  }
  if (!response.ok) {
    throw new Error(await errorMessage(response));
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
    throw new Error(await errorMessage(response));
  }
  return await response.json() as PairingStart;
}
