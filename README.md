# Free Cross-Device Audiobook

This repository implements the separately maintained v1.3 product specification.

Current status: stage 2 login, cross-device metadata/progress sync, offline recovery, and Mac pairing are implemented and verified.

## Fixed Decisions

- The Mac Agent is the default production TTS worker.
- GitHub Actions is limited to CI, deployment, and short feasibility tests.
- The project must not require a payment method or enable usage-based billing.
- New books are added on the Mac only.
- Real voice samples remain on the Mac by default.
- Rights-confirmed books and generated audio may be stored as public GitHub Release assets.
- Remote audio remains available until the user manually deletes it.

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

No TTS model is loaded while browsing, importing, logging in, or syncing. Long-form audio generation and publishing are the next implementation stage.

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
```

The Qwen benchmark uses the preserved local prototype environment. Generated references, parsed real books and runtime output stay under ignored local directories.
