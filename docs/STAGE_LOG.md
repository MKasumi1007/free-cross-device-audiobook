# Stage Log

## Stage 0: Free-Service and Base Feasibility

Started: 2026-07-16.

### Completed

- Confirmed all four preserved local prototype paths exist.
- Confirmed the workspace root was not already a Git repository.
- Confirmed the upstream `tts-audiobook-tool` worktree is clean at commit `2c49c9ec371fbf2e4e234eec9f3c91dfe6f1a66b`.
- Confirmed the upstream tool license is MIT.
- Confirmed installed `qwen-tts 0.1.1` reports Apache-2.0; Torch reports BSD-3-Clause; Transformers reports Apache-2.0.
- Confirmed Qwen 0.6B Base model files are cached locally.
- Confirmed Python 3.12 Qwen environment, Node, npm and ffmpeg are available.
- Recorded current GitHub/Firebase free-service terms in `FREE_SERVICES.md`.
- Fixed the production generator decision to `MAC_AGENT`; Actions is a bounded software test only.
- Created repository safety rules before using real data.
- Ran a private synthetic-reference Qwen 0.6B MPS benchmark successfully.
- Measured 16.064 seconds model load and 26.630 seconds generation for 7.680 seconds of audio (RTF 3.467).
- Measured peak system memory used at about 5.52 GiB; confirmed the benchmark process exited and the model was released.
- Validated the WAV as 24 kHz mono PCM with finite samples, -4.76 dBFS peak and -18.36 dBFS RMS.
- Passed the free-tier configuration audit and repository secret scan.
- Installed GitHub CLI 2.96.0 through Homebrew.
- Created the public `MKasumi1007/free-cross-device-audiobook` repository and pushed branch `main`.
- Rewrote local commit identity to the GitHub private no-reply address before the first public push.
- Added a repository-specific read/write deploy key after GitHub OAuth repeatedly returned HTTP 503; the private key remains only on the Mac.
- Passed the first public GitHub Actions CI run.
- Prepared a manual-only, 45-minute-bounded GitHub Actions CPU TTS experiment using only a synthetic voice.
- Prepared a public Release sine-wave probe for actual HTTP Range validation after repository creation.
- Published the synthetic Release probe and confirmed HTTP 206 with exactly 1,024 requested bytes.
- Ran the bounded GitHub CPU TTS experiment: setup and model download succeeded, but one short sentence did not finish after about 42 minutes of inference.
- Confirmed the 45-minute Actions hard timeout canceled the experiment after 2,720 total seconds.
- Reduced the retained manual compatibility workflow to a 15-minute limit and rejected Actions as a production TTS worker.

### Pending

- None. Firebase project creation belongs to stage 2 and has not started.

### Risks and Decisions

- Existing `work/sample_zh.*` has no recorded redistribution provenance, so it is not a public fixture.
- The user's real EPUB and real voice are excluded from stage 0.
- No cloud resource, billing account or paid service has been created.
- The baseline implies about 35 minutes generation per 10 minutes of audio and about 17.3 hours per 5-hour batch before retries; background scheduling and checkpoint recovery are essential.
- GitHub-hosted CPU inference is far too slow for long-form generation; production remains `MAC_AGENT` and no three-run cloud stability test will be spent.

## Stage 1: Parsing and Local Bookshelf

Completed: 2026-07-17.

### Completed

- Implemented EPUB 2 NCX, EPUB 3 Nav, nested TOC, multi-spine and no-TOC fallback parsing.
- Enforced 200 MiB source, 20,000 archive-entry, 1 GiB expanded-size, 200 MiB entry-size and 100:1 compression-ratio defaults.
- Rejected DRM/encryption metadata, path traversal, symbolic links, Zip Bombs, empty books, wrong magic and unsupported TXT encodings with Chinese reasons.
- Implemented BOM, UTF-8, UTF-16 and GB18030 TXT decoding with Chinese chapter-title inference.
- Added stable UUIDv5 chapter and paragraph IDs, footnote display/no-read behavior and ruby pronunciation de-duplication.
- Implemented five-hour batches, approximate ten-minute natural chunks, task states, priority values, leases, checkpoint fencing and the 48-hour replenishment rule.
- Added a loopback-only FastAPI Mac Agent that uses the native file picker, an exact Origin allowlist, one-time CSRF tokens and Private Network Access preflight support.
- Confirmed the browser cannot submit an arbitrary local path and the Agent does not scan directories or load TTS at startup.
- Added a React/Vite PWA with IndexedDB bookshelf/progress cache, chapter reader, rights labels and a Mac-only add-book entry.
- Verified a phone-width environment has no add/upload button and shows `请在 Mac 上添加新书`.
- Parsed the user-supplied real EPUB locally as `LOCAL_ONLY`: 150 chapters and 21,017 text segments. No parsed text or source file was committed or uploaded.
- Visually checked the desktop bookshelf, reader and rights/import dialog in Chrome.

### Verification

- Python: 21 tests passed across three EPUB structures, two TXT encodings, negative security samples, planning and Agent boundaries.
- Web: 2 responsive component tests passed.
- Ruff, strict mypy and TypeScript checks passed.
- Vite production PWA build passed.
- Repository secret scan and free-tier audit remain required by `npm test`.

### Free Services and Limits

- Stage 1 uses only local open-source dependencies and standard public-repository GitHub Actions.
- No Firebase project, Billing account, Storage, Functions or paid runner was created.
- The quality workflow is bounded to 15 minutes on a standard GitHub-hosted runner.

### Next

- Stage 2: Firebase Emulator Security Rules, optimistic progress sync, login and Mac Agent pairing.
- Create a real Firebase project only after emulator tests pass, then verify it remains Spark with no Billing link.

## Stage 2: Login, Sync, and Pairing

Completed: 2026-07-17.

### Completed

- Implemented Firebase Emulator Security Rules and indexes for owner isolation, books, chapters, bookmarks, progress, generation requests, audio metadata, pairing attempts, pairing requests, and worker links.
- Added optimistic progress versions so stale devices cannot overwrite a newer cloud position.
- Added Google popup login with redirect fallback, device registration, cloud bookshelf metadata, progress sync, usage estimates, and separate offline/quota/auth messages.
- Added IndexedDB cloud-book cache, pending-progress recovery, per-account cache isolation, and transaction-completion waits before closing the database.
- Added anonymous Firebase REST identity for the Mac Agent; its refresh token is stored only in macOS Keychain.
- Added automatic localhost pairing, six-digit hashed fallback codes, nine-minute expiry, five-attempt/ten-minute rate limiting, one-time binding, revocation, and reconnect after revocation.
- Hardened pairing so a signed-in user must consume a recent counted attempt before reading a pairing request.
- Fixed Python certificate discovery using the verified `certifi` CA bundle without disabling TLS verification.
- Created Firebase project `tingjian-shuye-audiobook`, confirmed Spark `$0`, and enabled only Google/Anonymous Authentication plus Firestore Standard in `eur3`.
- Kept Billing, Blaze, Storage, Functions, Firebase Hosting, Analytics, Gemini, and phone authentication disabled.
- Registered the Web app and added localhost plus the planned GitHub Pages domain to Authentication authorized domains.
- Completed a real Google web login and real automatic Mac pairing; the live Agent reported `configured: true` and `linked: true`.
- Deployed the tested Firestore Rules and indexes to the Spark project.

### Verification

- Web: 9 tests cover popup/redirect, desktop/mobile UI, connected-device revocation UI, IndexedDB offline recovery, cloud-cache account isolation, and metadata sync markers.
- Firestore Emulator: 12 tests cover unauthenticated/cross-user/forged-owner access, two-device bookshelf/progress behavior, worker limits, revocation, pairing expiry, brute-force limits, and reconnect.
- Python: 26 tests cover parsing, planning, Agent boundaries, anonymous REST identity, hashed pairing, active/revoked status, Keychain abstraction, and verified TLS.
- A real Firebase login and localhost auto-pairing succeeded without terminal tokens or payment setup.

### Free Services and Limits

- Firebase remains Spark and the console shows `免费（每月 0 美元）`; quota exhaustion must pause cloud sync rather than trigger an upgrade.
- GitHub remains a public repository using standard free Actions only.
- Mac generation remains the production plan; no cloud GPU, paid storage, Billing account, or payment method was introduced.

### Next

- Stage 3: local voice confirmation, checkpointed Mac generation, ten-minute audio chunks, GitHub Release publication, integrity verification, and idle memory release.

## Stage 3: Voice, Generation, and Publication

Completed: 2026-07-17.

### Completed

- Added private Mac voice selection for 10-30 second samples, 24 kHz mono normalization, transcript-bound versioning, preview, and explicit confirmation.
- Migrated the previously approved Qwen voice preview into private Application Support storage without publishing the sample or transcript.
- Added resumable segment checkpoints, chapter-safe roughly ten-minute M4A chunks, gzipped timelines, retry recovery, and stale-lock cleanup.
- Added a single Firestore worker with lease deadlines, fencing tokens, heartbeats, expired-task recovery, and idempotent ready metadata.
- Added AC-power and 2 GiB available-memory guards so the model is not loaded under unsafe conditions; the persistent Qwen child unloads after idle time.
- Added stable GitHub Release asset names, byte/hash verification, full-download verification, rollback on partial publication, and Range-request checks.
- Added web voice controls and a deterministic `生成约 5 小时音频` queue that does not reset existing tasks.
- Installed a self-contained private LaunchAgent runtime under macOS Application Support so the Agent starts without Terminal and avoids Documents-folder permission failures.
- Kept the user's current downloaded EPUB `LOCAL_ONLY`; no real book text, voice sample, transcript, or derived audio was published.

### Verification

- Unit and integration tests cover voice privacy, generation checkpoints, resource guards, process lifetime, cleanup, leases, Release publication, launchd installation, and web queue controls.
- Firestore Emulator rules reject worker theft and stale writes while allowing a correctly fenced expired-task recovery.
- A synthetic project-created audio/timeline pair was uploaded to a public Release, downloaded, hash/size checked, and served a real `206` Range response.
- The live Agent reports linked and configured while Qwen remains unloaded during idle operation.
- A fresh long preview was intentionally deferred when the memory guard detected less than 2 GiB available; the previously approved real Qwen preview remains available.

### Free Services and Limits

- Firebase remains Spark with no Billing account or payment method; only Authentication and Firestore are used.
- GitHub Pages, public Releases, and standard public-repository Actions remain the only remote services.
- Production speech generation uses the user's Mac. The app pauses on local resource pressure and never falls back to a paid GPU.
- Public Release assets remain available until explicit manual deletion; deletion cannot revoke copies already downloaded by others.

### Next

- Stage 4: bookshelf playback, chapter/timeline navigation, resume position, mobile controls, offline behavior, and real-device playback verification.

## Stage 4: Multi-Book Playback and Cross-Device Reader

Completed: 2026-07-17.

### Completed

- Added per-book audio and bookmark listeners, active-book priority 300, background-book priority 100, and automatic `INACTIVE_48_HOURS` pause/resume behavior.
- Added a persistent audio player with play/pause, 15-second seek, speed control, sleep timer, chapter and paragraph jumps, bookmarks, Media Session actions, and automatic chunk-to-chunk playback.
- Added timeline-driven paragraph highlighting and independent position, audio, chapter, and bookmark state when switching between books.
- Added five-second local position saves, cloud sync on key playback events, pending-sync recovery, and optimistic version conflict handling.
- Added clear Mac-off states: published audio remains playable, while missing audio waits for the Mac instead of failing or loading Qwen in the browser.
- Added owner-requested audio repair with a deletion-generation barrier so stale workers cannot overwrite a newer repair request.
- Added a four-minute Mac heartbeat and a ten-minute web online window.
- Added rights-gated remote book text for phone reading. Public text and timeline JSON now use the isolated `book-assets` branch because GitHub Release data downloads do not provide browser CORS; M4A audio remains in Releases.
- Kept the Service Worker limited to the app shell. EPUB, voice, parsed-book, timeline, and M4A files are not precached.
- Added deterministic desktop and mobile-width Playwright fixtures without shipping the synthetic M4A in the production PWA.
- Kept the user's real downloaded EPUB `LOCAL_ONLY`; no source, parsed text, generated audio, or voice data from it was uploaded.

### Verification

- Web: 16 tests passed for auth, storage, scheduling, worker heartbeat state, player behavior, and responsive app controls.
- Firestore Emulator: 19 tests passed, including inactive-book pause/resume, rights-gated text metadata, owner repair requests, worker repair fencing, pairing, isolation, and progress versions.
- Python: 54 tests passed, including browser-readable repository assets, deterministic text publication, generation recovery, worker heartbeat, and Release publication.
- Playwright: 6 routine tests passed across desktop Chrome and Pixel 7 emulation for playback, speed, sleep timer, bookmark feedback, two-book switching, reload resume, and responsive controls.
- A separate real-network Chrome smoke test fetched and decompressed project-created text and timeline files from `book-assets` and played the synthetic GitHub Release M4A without the Mac Agent.
- Production build, TypeScript, Ruff, strict mypy, dependency audit, free-tier audit, secret scan, and `git diff --check` passed.
- The generated Service Worker precache contains only six app-shell entries and no book, voice, timeline, or audio media.

### Free Services and Limits

- Firebase remains Spark with no Billing account or payment method; only Authentication and Firestore are used.
- GitHub Pages serves the app shell, the public data branch serves rights-confirmed text/timelines, and Releases serve public audio.
- Production TTS remains local to the Mac, one task at a time. No cloud GPU, paid storage, Firebase Hosting, Functions, or larger Actions runner was enabled.
- Remote assets remain public until manual deletion. Git history and third-party copies cannot be revoked.

### Residual Verification

- Pixel 7 emulation verifies mobile layout and browser behavior, but it is not a physical Android device.
- Physical iPhone Safari playback, lock-screen controls, interruption handling, and install-to-home-screen behavior remain a later real-device gate.

### Next

- Stage 5: retention/deletion controls, quota dashboard, reconciliation, and recovery UX.
