# Local Development Setup

Normal users should not need Terminal commands in the finished product. These commands are only for development while the one-click Mac launcher is still being built.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm install
```

Start the local Agent without loading the TTS model:

```bash
.venv/bin/audiobook-mac-agent
```

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

The anonymous Agent refresh token is stored only in macOS Keychain. Real books, voice samples, parsed text, generated audio, OAuth tokens, cookies, and account credentials must never be added to Git.

Firebase Hosting is intentionally unused. The production static site will use GitHub Pages; long-form audio generation remains local to the Mac.
