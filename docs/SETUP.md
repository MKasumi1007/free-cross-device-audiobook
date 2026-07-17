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

Firebase, Google login, public Pages and audio generation are not enabled in stage 1.
