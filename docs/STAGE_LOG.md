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

- Create and inspect a Firebase Spark project after user login.

### Risks and Decisions

- Existing `work/sample_zh.*` has no recorded redistribution provenance, so it is not a public fixture.
- The user's real EPUB and real voice are excluded from stage 0.
- No cloud resource, billing account or paid service has been created.
- The baseline implies about 35 minutes generation per 10 minutes of audio and about 17.3 hours per 5-hour batch before retries; background scheduling and checkpoint recovery are essential.
- GitHub-hosted CPU inference is far too slow for long-form generation; production remains `MAC_AGENT` and no three-run cloud stability test will be spent.
