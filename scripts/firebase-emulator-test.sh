#!/usr/bin/env bash
set -euo pipefail

if ! java -version >/dev/null 2>&1 && command -v brew >/dev/null 2>&1; then
  JAVA_PREFIX="$(brew --prefix openjdk@21)"
  export JAVA_HOME="${JAVA_PREFIX}/libexec/openjdk.jdk/Contents/Home"
  export PATH="${JAVA_PREFIX}/bin:${PATH}"
fi

exec firebase emulators:exec \
  --project demo-free-cross-device-audiobook \
  --only firestore \
  "npm run test --workspace @audiobook/firebase-rules-tests"
