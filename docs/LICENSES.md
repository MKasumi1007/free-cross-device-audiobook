# License Audit

Checked: 2026-07-16.

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
- FFmpeg is an external runtime; distribution configuration and enabled codecs must be audited before bundling it. v1 should call the user's Homebrew/system FFmpeg rather than redistribute a binary.

## Fixtures

- No real book or real voice is licensed as a fixture.
- The local system-voice Qwen benchmark is ignored and will not be uploaded.
- Future committed fixtures require an entry in `tests/fixtures/LICENSES.md` with a source URL and redistribution permission.

## Project License

The user has approved a public repository but has not selected a project license. Do not silently assign a license before repository creation; preserve all third-party notices regardless of the final choice.

