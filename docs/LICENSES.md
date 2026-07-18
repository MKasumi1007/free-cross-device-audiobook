# License Audit

Checked: 2026-07-18.

## Reused Local Prototype

- `tts-audiobook-tool` at commit `2c49c9ec371fbf2e4e234eec9f3c91dfe6f1a66b` includes an MIT license.
- Reused source must preserve its copyright notice and MIT license text.
- The upstream worktree was clean at preflight; this project will not modify it in place.

## Qwen3-TTS

- Installed package: `qwen-tts 0.1.1`.
- Installed package metadata: Apache-2.0.
- Official Qwen3-TTS repository: Apache-2.0.
- Model used locally: `Qwen/Qwen3-TTS-12Hz-0.6B-Base`.

Official source: <https://github.com/QwenLM/Qwen3-TTS>

## Core Runtime Metadata

- `torch 2.8.0`: BSD-3-Clause according to installed package metadata.
- `transformers 4.57.3`: Apache-2.0 according to installed package metadata.
- The installer prefers an existing system FFmpeg. If it is absent on Apple Silicon, it downloads the pinned `imageio-ffmpeg` 0.6.0 macOS arm64 wheel from PyPI, verifies both the wheel and extracted FFmpeg 7.1 executable with repository-pinned SHA-256 values, and keeps the executable plus `FFMPEG-NOTICE.txt` under the private Application Support tools directory. The executable reports a GPL-enabled build without `--enable-nonfree`; source and license links are retained in that notice. The Python wrapper is BSD-2-Clause; the extracted FFmpeg executable remains governed by its applicable FFmpeg license.

## Stage 1 Application Dependencies

Installed package metadata and included license files report permissive licenses:

- Beautiful Soup: MIT.
- defusedxml: Python Software Foundation license.
- FastAPI: MIT.
- Uvicorn: BSD-3-Clause.
- React and React DOM: MIT.
- Vite and the React Vite plugin: MIT.
- Zod: MIT.
- vite-plugin-pwa: MIT.

Development-only test and lint tools are not bundled into the web application.

## Fixtures

- No real book or real voice is licensed as a fixture.
- Stage 1 parser fixtures are short project-authored texts generated at test time.
- The local system-voice Qwen benchmark is ignored and will not be uploaded.
- Future committed fixtures require an entry in `tests/fixtures/LICENSES.md` with a source URL and redistribution permission.

## Project License

The user has approved a public repository but has not selected a project license. Do not silently assign a license before repository creation; preserve all third-party notices regardless of the final choice.
