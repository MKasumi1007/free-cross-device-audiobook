# Free Services Audit

Last checked: 2026-07-17.

This document records official terms relevant to the hard requirement: no payment method, no usage-based billing, and no automatic charge. Terms must be checked again on the day each resource is created.

## Decision Summary

| Service | Current decision | Payment safety | Product role |
| --- | --- | --- | --- |
| GitHub Pages | Allowed | Public repository feature; no paid upgrade planned | Static PWA only |
| GitHub Releases | Allowed with public-data warning | Public release assets; no payment method needed for the planned public repository | Generated audio for rights-confirmed books |
| GitHub public data branch | Allowed with public-data warning | Normal public repository storage; no paid add-on enabled | Rights-confirmed parsed text and timelines |
| Standard GitHub-hosted Actions runner | CI/test/deploy only | Free for public repositories; larger runners are charged | CI, Pages, small TTS experiment |
| Firebase Spark | Allowed after console verification | Official docs say no payment information is needed; quota exhaustion shuts off the product | Auth and Firestore metadata |
| Firebase Blaze / Cloud Billing | Forbidden | Pay as you go and requires billing linkage | None |
| Modal / rented GPU | Forbidden | Requires payment information or can charge by usage | None |

## Firebase Spark

Official pricing-plan documentation says the Spark plan needs no payment information. It includes no-cost quotas for Firestore, and exceeding a no-cost quota on Spark shuts that product off until the next cycle unless the project is upgraded. Linking a Cloud Billing account can automatically upgrade a project to Blaze, so this project must never link Billing.

Current documented Firestore free quota for one database per project:

- 1 GiB stored data.
- 50,000 document reads per day.
- 20,000 document writes per day.
- 20,000 document deletes per day.
- 10 GiB outbound transfer per month.

TTL deletes, point-in-time recovery, backups, restores, clones, additional databases and paid Google Cloud products are excluded from the free design.

### Created-project verification

- Project: `tingjian-shuye-audiobook` (`Tingjian Shuye Audiobook`).
- Firebase console visibly reports `结算方案：Spark` and `免费（每月 0 美元）`.
- No payment method or Cloud Billing account was linked; Blaze was not enabled.
- Enabled: Authentication with Google and Anonymous providers; Firestore Standard edition in `eur3`.
- Not enabled or created: Cloud Storage, Cloud Functions, Firebase Hosting, Analytics, Gemini, phone authentication, paid Google Cloud products.
- Firestore Security Rules and indexes were deployed only after Emulator tests passed.
- The app records its own estimated Firestore reads/writes and degrades locally on network or quota errors; it never upgrades the plan.
- The local safety thresholds stop at 45,000 estimated reads, 18,000 writes, or 18,000 deletes per UTC day, below the documented Spark quotas; the estimate resets the next day.

The Firebase Web config in `config/firebase-public-config.json` is public client configuration. Firebase's official documentation says these Firebase API keys identify the project/app and are not backend authorization; Authentication and Security Rules enforce access. No OAuth token, refresh token, password, cookie, private key, or Firebase CLI credential is stored in the repository.

Sources:

- <https://firebase.google.com/docs/projects/billing/firebase-pricing-plans>
- <https://firebase.google.com/docs/firestore/pricing>
- <https://firebase.google.com/docs/projects/api-keys>

## GitHub Actions

Official billing documentation says standard GitHub-hosted runners are free for public repositories. Larger runners are always charged. The public standard Ubuntu runner currently has 4 CPUs, 16 GB RAM and 14 GB SSD according to the runner documentation.

GitHub's additional-product terms restrict hosted Actions to development, testing, deployment, or publication of the repository's software. They also prohibit disproportionate server burden and using Actions as a generic serverless application. Therefore:

- Production audiobook generation is assigned to the Mac Agent.
- Actions TTS is a short, bounded software feasibility test only.
- A production `tts-worker.yml` remains disabled unless GitHub policy is rechecked and all v1.3 gates pass.
- The user's Mac will never be attached to the public repository as a self-hosted runner.

Sources:

- <https://docs.github.com/en/billing/concepts/product-billing/github-actions>
- <https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job>
- <https://docs.github.com/en/actions/reference/limits>
- <https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features>

## GitHub Releases

Official documentation currently states that one release can have up to 1,000 assets, each asset must be under 2 GiB, and there is no documented total release-size or bandwidth quota. This is not a promise of permanent storage or CDN behavior. Assets in the planned public repository are public.

Design safeguards:

- Keep audio near ten minutes per asset and far below 2 GiB.
- Split books across releases before approaching 1,000 assets.
- Verify Range, 206, seeking, rate changes and iPhone playback in practice.
- On 403, 404, 429, throttling or policy change, degrade instead of opening paid storage.
- Never upload a book without a recorded rights confirmation.

Source: <https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases>

## GitHub Pages and Public Data Branch

Current documented limits include a 1 GiB published-site maximum, a 10-minute deployment timeout and a soft 100 GiB monthly bandwidth limit. Pages hosts only the static app shell. Rights-confirmed text and timeline JSON use an isolated public `book-assets` branch so browsers can read them with CORS; M4A audio remains in Releases. `LOCAL_ONLY` books and voice data never enter either location.

Source: <https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits>

## Current Verification State

- GitHub repository `MKasumi1007/free-cross-device-audiobook` is public and uses standard public-repository Actions only.
- Firebase CLI is authenticated locally; its credentials are outside the repository.
- Real Google web login and real Mac-to-owner pairing were completed successfully on 2026-07-17.
- The Agent refresh token was verified present in macOS Keychain without printing its value.
- Authorized domains currently include localhost development and the planned `mkasumi1007.github.io` Pages domain.
- A project-created synthetic Qwen smoke asset was published to a public GitHub Release, downloaded in full, hash/size checked, and fetched with a real HTTP `206` Range response.
- The production Mac worker uses local CPU/RAM only; its power and memory guard pauses work rather than requesting paid compute.
- A real Chrome run fetched and decompressed project-created text and timeline fixtures from `book-assets`, then played the synthetic Release M4A while the Mac Agent was not involved.
- The Firebase project still uses only Spark Authentication and Firestore. No Storage, Functions, Hosting, Billing, Blaze, or payment method was added for stage 5.
- Stage 5 deletion uses the existing Mac Agent and GitHub APIs. Rate limits pause with exponential backoff; reconciliation reports orphan assets but never deletes them automatically.
