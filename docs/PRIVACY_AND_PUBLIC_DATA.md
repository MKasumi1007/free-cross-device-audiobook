# Privacy, Public Data, and Rights

## Public Only After Confirmation

- Source code and project documentation.
- Synthetic, public-domain, or redistribution-licensed fixtures.
- Books the user confirms they may publicly redistribute.
- Audio, timelines, covers, and parsed text derived from those books.

The required confirmation is:

> 我确认拥有公开传播或再分发这本书及生成音频的权利。

Downloaded, unknown-origin, or unclear-rights books remain `LOCAL_ONLY`.

## Never Public

- Passwords, API keys, access tokens, refresh tokens, personal access tokens and secrets.
- Verification codes, private keys, cookies, sessions and macOS Keychain data.
- Firebase or GitHub account credentials.
- Real voice samples unless the user separately confirms publication risk.
- Books and generated audio without a rights confirmation.

## Voice Default

The real voice sample stays on the Mac. The Mac Agent uses it locally and uploads only the generated audio. Cloud voice encryption and Actions voice generation are not v1 requirements.

## Public-Asset Warning

GitHub Release assets in a public repository can be copied by anyone. Deleting an asset cannot revoke third-party downloads or caches.

