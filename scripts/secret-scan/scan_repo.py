#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", ".local", ".venv", "dist", "node_modules", "private", "runtime-data"}
SKIP_SUFFIXES = {
    ".aiff",
    ".epub",
    ".flac",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".m4b",
    ".mp3",
    ".pdf",
    ".png",
    ".wav",
    ".webp",
}
PATTERNS = {
    "GitHub token": re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),
    "GitHub fine-grained token": re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "assigned refresh token": re.compile(r"(?i)refresh[_-]?token\s*[:=]\s*['\"][^'\"]{12,}"),
    "assigned secret": re.compile(r"(?i)(?:client[_-]?secret|firebase[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}"),
}


def candidates() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES or path.stat().st_size > 2_000_000:
            continue
        result.append(path)
    return result


def main() -> int:
    findings: list[str] = []
    for path in candidates():
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{path.relative_to(ROOT)}:{line}: {label}")

    forbidden_files = [
        path.relative_to(ROOT)
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(part in SKIP_DIRS for part in path.parts)
        and any(
            token in path.name.lower()
            for token in ("service-account", "firebase-adminsdk", ".p12", ".pem")
        )
        and ".git" not in path.parts
    ]
    findings.extend(f"{path}: forbidden credential-like filename" for path in forbidden_files)

    if findings:
        print("Secret scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print(f"Secret scan passed: checked {len(candidates())} text files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
