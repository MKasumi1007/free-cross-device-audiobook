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
- Initialized a local Git repository on branch `main`; no public repository has been created.

### Pending

- Complete GitHub authentication without exposing credentials.
- Ask for the public repository name immediately before creation.
- Run the GitHub Actions and Release tests after the public repository exists.
- Create and inspect a Firebase Spark project after user login.

### Risks and Decisions

- Existing `work/sample_zh.*` has no recorded redistribution provenance, so it is not a public fixture.
- The user's real EPUB and real voice are excluded from stage 0.
- No cloud resource, billing account or paid service has been created.
- The baseline implies about 35 minutes generation per 10 minutes of audio and about 17.3 hours per 5-hour batch before retries; background scheduling and checkpoint recovery are essential.
