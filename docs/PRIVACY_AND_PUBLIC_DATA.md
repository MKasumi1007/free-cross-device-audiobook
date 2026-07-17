# Privacy, Public Data, and Rights

## Public Only After Confirmation

- Source code and project documentation.
- Synthetic, public-domain, or redistribution-licensed fixtures.
- Books the user confirms they may publicly redistribute.
- Audio, timelines, covers, and parsed text derived from those books.

The required confirmation is:

> 我确认拥有公开传播或再分发这本书及生成音频的权利。

Downloaded, unknown-origin, or unclear-rights books remain `LOCAL_ONLY`.

## Public Firebase Client Configuration

`config/firebase-public-config.json` contains the Firebase Web app identifier, project identifier, auth domain, and Firebase-provisioned Web API key. Firebase documents this configuration as public by design: it identifies the app but does not authorize access. Firestore Authentication and Security Rules provide authorization.

This exception applies only to the Firebase-provisioned Web client configuration used for Firebase services. It does not make other API keys safe to publish.

## Never Public

- Passwords, secret or non-Firebase API keys, access tokens, refresh tokens, personal access tokens and secrets.
- Verification codes, private keys, cookies, sessions and macOS Keychain data.
- Firebase or GitHub account credentials.
- Real voice samples unless the user separately confirms publication risk.
- Voice transcripts, source paths, voice hashes, preview files, and normalization artifacts.
- Books and generated audio without a rights confirmation.

## Voice Default

The real voice sample and its transcript stay on the Mac in private application storage. The browser can read only safe status fields and a loopback preview stream; it never receives a source path, transcript, hash, or credential. The Mac Agent uses the confirmed voice locally and uploads only generated audio for a rights-confirmed book. Cloud voice encryption and Actions voice generation are not v1 requirements.

Generation is denied for `LOCAL_ONLY` books. The currently imported downloaded EPUB remains in that state, so neither its text nor any derived audio is uploaded.

## Public-Asset Warning

GitHub Release assets in a public repository can be copied by anyone. Deleting an asset cannot revoke third-party downloads or caches.
