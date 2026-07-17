import { expect, test } from "@playwright/test";

const AUDIO_URL = "https://github.com/MKasumi1007/free-cross-device-audiobook/releases/download/book-stage3-public-domain-smoke/audio-synthetic-smoke-634420850d84.m4a";
const TEXT_URL = "https://raw.githubusercontent.com/MKasumi1007/free-cross-device-audiobook/book-assets/books/stage4-public-domain-smoke/book-stage4-public-domain-smoke-text-93a28fe00ee4.json.gz";
const TIMELINE_URL = "https://raw.githubusercontent.com/MKasumi1007/free-cross-device-audiobook/book-assets/books/stage4-public-domain-smoke/timeline-stage4-synthetic-chunk-4b2042c42804.json.gz";

test.skip(process.env.RUN_REMOTE_SMOKE !== "1", "Manual real GitHub Release verification");

test("real public Release text is fetchable and audio plays without the Mac Agent", async ({ page }) => {
  await page.goto("?e2e=player");
  const remoteIds = await page.evaluate(async ({ textUrl, timelineUrl }) => {
    const readGzip = async (url: string): Promise<Record<string, unknown>> => {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`data HTTP ${response.status}`);
      const stream = response.body?.pipeThrough(new DecompressionStream("gzip"));
      if (!stream) throw new Error("missing response stream");
      return JSON.parse(await new Response(stream).text()) as Record<string, unknown>;
    };
    const [book, timeline] = await Promise.all([readGzip(textUrl), readGzip(timelineUrl)]);
    return { bookId: book.book_id, chunkId: timeline.chunk_id };
  }, { textUrl: TEXT_URL, timelineUrl: TIMELINE_URL });
  expect(remoteIds).toEqual({
    bookId: "stage4-public-domain-smoke",
    chunkId: "stage4-synthetic-chunk",
  });

  await page.evaluate((url) => {
    const audio = document.createElement("audio");
    audio.id = "remote-smoke-audio";
    audio.src = url;
    audio.preload = "auto";
    const button = document.createElement("button");
    button.id = "remote-smoke-play";
    button.textContent = "Play remote smoke";
    button.style.cssText = "position:fixed;top:8px;right:8px;z-index:99999";
    button.onclick = () => void audio.play();
    document.body.append(audio, button);
  }, AUDIO_URL);
  await page.locator("#remote-smoke-play").click();
  await expect.poll(
    () => page.locator("#remote-smoke-audio").evaluate((audio: HTMLAudioElement) => audio.currentTime),
    { timeout: 15_000 },
  ).toBeGreaterThan(0);
});
