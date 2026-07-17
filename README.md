# Free Cross-Device Audiobook

This repository implements the separately maintained v1.3 product specification.

中文项目说明、使用方法、架构、隐私边界和维护手册见
[`docs/项目完整说明.md`](docs/项目完整说明.md)。

Current status: stage 5 plus the owner-only private-library extension are implemented and verified. Physical iPhone acceptance remains outstanding.

## Fixed Decisions

- The Mac Agent is the default production TTS worker.
- GitHub Actions is limited to CI, deployment, and short feasibility tests.
- The project must not require a payment method or enable usage-based billing.
- New books are added on the Mac only.
- Real voice samples remain on the Mac by default.
- Rights-confirmed parsed text and timelines may be stored on the public `book-assets` branch; generated audio may be stored as public GitHub Release assets.
- `LOCAL_ONLY` text, timelines, and audio use owner-only Firestore documents and never enter GitHub.
- Generated audio enters safe deletion after five days when the paired Mac is next online; the user may delete it earlier.

No real book, voice sample, generated audiobook, credential, or model cache belongs in Git history.

## Implemented

- Safe EPUB 2/3 and TXT parsing with real chapters and paragraph text.
- Stable chapter and text-segment IDs across unchanged re-imports.
- Mac-only native file selection through an origin-restricted loopback Agent.
- IndexedDB bookshelf and reading position cache.
- Google popup login with redirect fallback, per-device registration, and Firestore sync.
- Optimistic progress versions so an old device cannot overwrite a newer position.
- Anonymous Mac Agent identity, six-digit short-lived pairing, rate limiting, revocation, and reconnect.
- Mac refresh tokens stored only in macOS Keychain.
- Responsive React PWA: desktop can add books; mobile shows `请在 Mac 上添加新书`.
- Five-hour generation batches split near natural ten-minute boundaries.
- Rights remain `LOCAL_ONLY` unless the user explicitly confirms public-distribution rights.
- Private 10-30 second voice selection, normalization, versioning, preview, and explicit confirmation on the Mac.
- Checkpointed segment generation with chapter-safe audio chunks, timelines, retry recovery, and stale-lock cleanup.
- A single background generation worker with lease fencing, AC-power and memory guards, and idle TTS-model unloading.
- Idempotent GitHub Release publication with stable asset names, byte/hash verification, and HTTP Range verification.
- A private LaunchAgent runtime under macOS Application Support so normal use does not require Terminal.
- Active-book generation priority, 48-hour inactive-book pause/resume, and independent state for multiple books.
- A persistent player with chapter jumps, paragraph highlighting, 15-second seek, speed control, sleep timer, bookmarks, Media Session controls, and automatic chunk-to-chunk playback.
- Frequent local progress saves plus version-fenced cloud sync on key playback events.
- Browser-readable, SHA-256-verified public text and timeline data on an isolated GitHub branch.
- Desktop and mobile-width Playwright coverage, including two-book switching and reload resume.
- An `音频空间` dashboard with per-library, book, chapter, and chunk byte/duration totals.
- Two-phase remote deletion with generation barriers, idempotent retries, and explicit irreversible confirmation.
- Regeneration from the deleted chunk's preserved text cursor without deleting books, text, bookmarks, progress, or voice data.
- Conservative Spark quota pauses, GitHub rate-limit backoff, six-hour local cleanup, and report-only remote reconciliation.
- Five-day remote-audio retention enforced by both the Mac worker and Firestore Rules; deletion preserves books, text, bookmarks, progress, voice data, and regeneration cursors.
- Owner-only Firestore assets split into 512 KiB parts, verified by SHA-256, capped at 32 MiB each and 700 MiB total.
- Private book text and the current private audio chunk load on signed-in phones without the Mac or Qwen running.

No TTS model is loaded while browsing, importing, logging in, syncing, playing existing audio, or waiting for work. The model starts only for an eligible generation task and unloads after four idle minutes. The current downloaded EPUB remains private: it may be generated only into the signed-in owner's Firestore area and cannot be published to GitHub without a separate rights confirmation.

The active Firebase project remains on Spark (`$0`) with no Billing account. Only Authentication and Firestore are used; Storage, Functions, Firebase Hosting, Analytics, and Gemini are not enabled.

## Development Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm install
npm test
npm run build
npm run dev
.venv/bin/audiobook-mac-agent
.venv/bin/audiobook-install-agent
```

The Qwen benchmark uses the preserved local prototype environment. Generated references, parsed real books and runtime output stay under ignored local directories.
