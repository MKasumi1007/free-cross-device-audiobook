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
