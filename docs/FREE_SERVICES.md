# Free Services Audit

Last checked: 2026-07-16.

This document records official terms relevant to the hard requirement: no payment method, no usage-based billing, and no automatic charge. Terms must be checked again on the day each resource is created.

## Decision Summary

| Service | Stage 0 decision | Payment safety | Product role |
| --- | --- | --- | --- |
| GitHub Pages | Allowed | Public repository feature; no paid upgrade planned | Static PWA only |
| GitHub Releases | Allowed with public-data warning | Public release assets; no payment method needed for the planned public repository | Rights-confirmed books and audio |
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

Sources:

- <https://firebase.google.com/docs/projects/billing/firebase-pricing-plans>
- <https://firebase.google.com/docs/firestore/pricing>

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

## GitHub Pages

Current documented limits include a 1 GiB published-site maximum, a 10-minute deployment timeout and a soft 100 GiB monthly bandwidth limit. Pages hosts only the static app shell; books and audio stay in Releases.

Source: <https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits>

## Unverified Until Login

- GitHub CLI 2.96.0 is installed, but no GitHub account is authenticated yet; repository settings have not been inspected.
- No Firebase project exists for this implementation, so Spark/Billing state has not been verified in the console.
- No cloud resource has been created.
