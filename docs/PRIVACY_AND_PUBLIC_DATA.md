# Privacy, Public Data, and Rights

## Public Only After Confirmation

- Source code and project documentation.
- Synthetic, public-domain, or redistribution-licensed fixtures.
- Books the user confirms they may publicly redistribute.
- Audio, timelines, covers, and parsed text derived from those books. Parsed text and timelines use the public `book-assets` branch because browsers cannot directly fetch Release data files across origins.

The required confirmation is:

> 我确认拥有公开传播或再分发这本书及生成音频的权利。

Downloaded, unknown-origin, or unclear-rights books remain `LOCAL_ONLY`.

`LOCAL_ONLY` means “never public,” not “cannot be heard.” Its compressed text, timelines, and generated audio may be split into owner-only Firestore documents. Firestore Rules allow only the signed-in owner and that owner's currently linked Mac Agent to read them. They never receive a public URL and never enter GitHub Releases, Pages, the `book-assets` branch, Actions artifacts, or Git history.

## Public Firebase Client Configuration

`config/firebase-public-config.json` contains the Firebase Web app identifier, project identifier, auth domain, and Firebase-provisioned Web API key. Firebase documents this configuration as public by design: it identifies the app but does not authorize access. Firestore Authentication and Security Rules provide authorization.

This exception applies only to the Firebase-provisioned Web client configuration used for Firebase services. It does not make other API keys safe to publish.

## Never Public

- Passwords, secret or non-Firebase API keys, access tokens, refresh tokens, personal access tokens and secrets.
- Verification codes, private keys, cookies, sessions and macOS Keychain data.
- Firebase or GitHub account credentials.
- Real voice samples unless the user separately confirms publication risk.
- Voice transcripts, source paths, voice hashes, preview files, and normalization artifacts.
- Books and generated audio without a rights confirmation must never be public.

## Voice Default

The real voice sample and its transcript stay on the Mac in private application storage. The browser can read only safe status fields and a loopback preview stream; it never receives a source path, transcript, hash, or credential. The Mac Agent uses the confirmed voice locally. Rights-confirmed output may go to public GitHub assets; `LOCAL_ONLY` output may go only to owner-authorized Firestore documents. The voice sample itself is never uploaded.

Public generation is denied for `LOCAL_ONLY` books. Private generation requires the matching `PRIVATE_FIRESTORE` task and publisher; the pipeline rejects any storage-mode mismatch. The currently imported downloaded EPUB remains in that state, so neither its text nor any derived audio can be uploaded to public GitHub.

Private files are SHA-256 checked, split into at most 512 KiB per Firestore document, limited to 32 MiB per logical file, and stopped at 700 MiB total private assets. The browser temporarily joins only the current roughly ten-minute audio chunk and revokes its object URL when switching. Qwen remains on the Mac and unloads after four idle minutes.

## Public-Asset Warning

GitHub Release assets and files on the public `book-assets` branch can be copied by anyone. Deleting a remote asset cannot revoke third-party downloads, Git history, or caches.
