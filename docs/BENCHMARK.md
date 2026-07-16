# Stage 0 Benchmark

Status: Mac baseline complete; GitHub experiment pending repository creation.

## Machine

- macOS 15.6.1 on arm64.
- 8 logical CPUs.
- 8 GiB unified memory.
- Power state at preflight: AC attached.
- Existing Qwen environment: Python 3.12, `qwen-tts 0.1.1`, `torch 2.8.0`.
- Cached model: `Qwen/Qwen3-TTS-12Hz-0.6B-Base`.

## Safety Rules

- Do not use the user's real voice or a real book.
- Generate a temporary Chinese reference with the macOS system voice.
- Store all benchmark audio and JSON under `.local/benchmark/`.
- Never add benchmark audio to Git until its redistribution rights are separately proven.
- Run one generation at a time and unload the model after completion.

## Mac Baseline Result

Run date: 2026-07-16.

Input safety: temporary macOS Tingting system voice and a purpose-written test sentence. No real user voice and no book text were used. Files remain under ignored `.local/benchmark/`.

| Measurement | Result |
| --- | ---: |
| Model load | 16.064 seconds |
| Generation | 26.630 seconds |
| Output duration | 7.680 seconds |
| Generation RTF | 3.467 |
| Total process time | 43.010 seconds |
| Peak process RSS | 694 MiB |
| Peak system memory used | 5.52 GiB |
| Output | 24 kHz, mono, PCM s16 WAV |
| Output size | 368,684 bytes |
| Peak level | -4.76 dBFS |
| RMS level | -18.36 dBFS |

No NaN/Inf audio sample warning was reported. The benchmark process exited successfully and no Qwen process remained.

Conservative linear estimate before optimization:

- Ten minutes of audio: about 34.7 minutes of generation.
- Five hours of audio: about 17.3 hours of generation.

These estimates exclude model load, encoding, retries and thermal throttling. Long-form benchmarks must checkpoint every natural segment and should run only when system memory and power rules allow.

Pending GitHub experiment after repository confirmation:

- Install and model-download time.
- Peak runner memory and disk use.
- Three-run stability if the first bounded experiment is acceptable.
- Policy decision. Production remains `MAC_AGENT` regardless of experiment success unless v1.3 is explicitly revised.

## Decision

Default production generator: `MAC_AGENT`.

Reason: it meets the no-payment requirement and GitHub Actions terms do not support treating hosted runners as a general audiobook compute backend.

