# Free Cross-Device Audiobook

This repository implements the separately maintained v1.3 product specification.

Current status: stage 0 feasibility and safety validation.

## Fixed Decisions

- The Mac Agent is the default production TTS worker.
- GitHub Actions is limited to CI, deployment, and short feasibility tests.
- The project must not require a payment method or enable usage-based billing.
- New books are added on the Mac only.
- Real voice samples remain on the Mac by default.
- Rights-confirmed books and generated audio may be stored as public GitHub Release assets.
- Remote audio remains available until the user manually deletes it.

No real book, voice sample, generated audiobook, credential, or model cache belongs in Git history.

## Stage 0 Commands

```bash
python3 scripts/secret-scan/scan_repo.py
python3 scripts/free-tier-audit/check_forbidden_config.py
```

The Qwen benchmark uses the existing Python 3.12 environment under the preserved local prototype. Its generated reference and output stay under `.local/`, which is ignored by Git.
