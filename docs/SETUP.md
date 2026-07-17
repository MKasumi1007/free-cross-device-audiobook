# Local Development Setup

Normal use does not require Terminal. The installed private LaunchAgent starts the Mac Agent after login, and the web page provides book, voice, and generation controls. The commands below are only for development or repair.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm install
```

Start the local Agent without loading the TTS model:

```bash
.venv/bin/audiobook-mac-agent
```

Install or repair the background Agent:

```bash
.venv/bin/audiobook-install-agent
```

When run from the project directory, the installer updates the existing private runtime and public Firebase configuration under `~/Library/Application Support/听见书页/`, writes a private LaunchAgent property list, and starts it. It does not copy books, voice samples, credentials, or generated audio into the repository.

Start the web development server in another process:

```bash
npm run dev
```

Open <http://127.0.0.1:5173/free-cross-device-audiobook/>. Only the explicit origins in `mac_agent.security.DEFAULT_ALLOWED_ORIGINS` can call the Agent. Runtime books are written under ignored `runtime-data/`; the Agent never accepts an arbitrary file path from the browser.

The checked-in `config/firebase-public-config.json` is the public Firebase Web configuration for project `tingjian-shuye-audiobook`. It is not an account credential. Access is enforced by Firebase Authentication and the deployed Firestore Security Rules.

Current development flow:

1. Open the web page and click `登录同步`.
2. Choose the project Google account.
3. Start the Mac Agent, then click `连接这台 Mac`.
4. Automatic localhost pairing is preferred. If it is unavailable, enter the six-digit code shown by the Agent.
5. A connected Agent appears as `Mac 已连接`; clicking it opens the explicit revocation dialog.

Normal voice and generation flow:

1. Open `我的声音`, select a clear 10-30 second recording, and enter exactly what was spoken.
2. Listen to the private preview and click `确认使用这个声音` only after it sounds right.
3. Open a book and click `生成约 5 小时音频`. A rights-confirmed book publishes to GitHub; a `LOCAL_ONLY` book shows `私密生成约 5 小时音频` and uses only the signed-in owner's Firestore area.
4. The background Agent processes one task at a time. It pauses instead of loading Qwen when the Mac is on battery or has less than 2 GiB available memory.
5. A failed or restarted task resumes from its segment checkpoint. Public and private assets are hash-verified before Firestore marks them ready; local WAV/M4A files are then removed and Qwen unloads after four idle minutes.
6. Each ready audio chunk is retained for five days from its completion time. The paired Mac checks hourly while online and queues verified deletion; if the Mac is off at the deadline, deletion happens after it next starts.

Normal listening flow:

1. Open a book from the shelf and choose a chapter or paragraph.
2. The bottom player starts as soon as the first audio chunk is ready; already published public or private audio plays even when the Mac is off. Private audio requires the owner's login.
3. Use 15-second seek, speed, sleep timer, bookmarks, or the phone lock-screen controls as needed.
4. Position is saved locally during playback and synced on pause, chapter jump, chunk completion, and other key events.
5. Opening another book gives it generation priority. A queued book not opened for 48 hours pauses automatically and resumes when reopened.
6. If an asset is missing or damaged, use `重新准备`; the request waits safely until the Mac Agent can repair it.

Normal audio-space flow:

1. Open `音频空间` in the top bar, or open a book and click `管理已生成音频`.
2. Review the total occupied bytes and duration, then select all books or one book.
3. Audio is automatically queued for deletion five days after generation, or it can be deleted earlier by chunk, chapter, book, or the full library. A final dialog states the exact scope and size before a manual request is queued.
4. The Mac Agent deletes either the public GitHub pair or the owner-only Firestore parts, verifies they are absent, then changes the row from `删除中` to `已删除`. The voice model is not loaded for this work.
5. Books, chapter text, bookmarks, reading progress, voice samples, hashes, sizes, and the original text cursor remain untouched.
6. Click `从原位置重新生成` later to recreate only that chunk from its preserved starting paragraph.
7. Repeated deletion requests are idempotent. A stale upload cannot cross the deletion-generation barrier and restore deleted audio.

If the conservative daily Spark estimate is reached, or Firebase reports quota exhaustion, cloud listeners pause for the rest of the UTC day. Local reading and progress continue, and sync automatically becomes eligible again the next day. GitHub 403/429 limits use delayed retries and never trigger a paid upgrade.

The anonymous Agent refresh token is stored only in macOS Keychain. Real books, voice samples, parsed text, generated audio, OAuth tokens, cookies, and account credentials must never be added to Git.

Firebase Hosting and Storage are intentionally unused. The production static site uses GitHub Pages; browser-readable text and timelines for rights-confirmed books use the public `book-assets` branch, and audio uses public Releases. `LOCAL_ONLY` assets use owner-only Firestore documents with a 700 MiB hard ceiling. Long-form generation remains local to the Mac. Audio is removed after the five-day retention window when the Mac is online, can be removed earlier manually, and is never moved to a paid service automatically.
