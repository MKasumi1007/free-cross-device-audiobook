export function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function verifySha256(
  bytes: ArrayBuffer,
  expectedSha256: string,
): Promise<void> {
  const digest = hex(await crypto.subtle.digest("SHA-256", bytes));
  if (expectedSha256 && digest !== expectedSha256) throw new Error("REMOTE_ASSET_HASH_MISMATCH");
}

export async function decodeGzipJson(compressed: ArrayBuffer): Promise<unknown> {
  const bytes = new Uint8Array(compressed);
  let text: string;
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
    if (typeof DecompressionStream === "undefined") throw new Error("GZIP_UNSUPPORTED");
    const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream("gzip"));
    text = await new Response(stream).text();
  } else {
    text = new TextDecoder().decode(compressed);
  }
  return JSON.parse(text) as unknown;
}

export async function fetchVerifiedGzipJson(
  url: string,
  expectedSha256: string,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(url, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("REMOTE_ASSET_UNAVAILABLE");
  const compressed = await response.arrayBuffer();
  await verifySha256(compressed, expectedSha256);
  return decodeGzipJson(compressed);
}
