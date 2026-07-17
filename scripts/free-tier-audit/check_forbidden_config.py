#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
JSON_FILES = (
    "package.json",
    "firebase.json",
    ".firebaserc",
    "config/firebase-public-config.json",
)
FORBIDDEN_KEYS = {
    "billingAccount",
    "billing_account",
    "blaze",
    "cloudFunctions",
    "cloudRun",
    "codespaces",
    "larger-runner",
    "modal",
}


def walk_json(value: object, location: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_KEYS:
                errors.append(f"{location}: forbidden paid-capability key {key!r}")
            walk_json(child, f"{location}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_json(child, f"{location}[{index}]", errors)


def main() -> int:
    errors: list[str] = []
    for relative in JSON_FILES:
        path = ROOT / relative
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{relative}: invalid JSON: {exc}")
            continue
        walk_json(data, relative, errors)

    workflows = ROOT / ".github" / "workflows"
    if workflows.exists():
        for path in workflows.glob("*.y*ml"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "self-hosted" in text:
                errors.append(f"{path.relative_to(ROOT)}: public repo cannot use self-hosted")
            if "runs-on:" in text and any(
                label in text for label in ("-large", "gpu", "larger-runner")
            ):
                errors.append(f"{path.relative_to(ROOT)}: possible paid runner label")

    if errors:
        print("Free-tier audit failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Free-tier audit passed: no configured paid capability found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
