from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path

from mac_agent.release_assets import GitHubReleasePublisher


class FakePublisher(GitHubReleasePublisher):
    def __init__(self, content: bytes) -> None:
        super().__init__("owner/repository")
        self.content = content

    def _download(self, asset_id: int, target: Path) -> subprocess.CompletedProcess[bytes]:
        assert asset_id == 479719693
        target.write_bytes(self.content)
        return subprocess.CompletedProcess([], 0, b"", b"")


def test_release_asset_uses_numeric_rest_id_and_browser_download_url(tmp_path: Path) -> None:
    content = b"synthetic public test audio"
    publisher = FakePublisher(content)
    asset = {
        "id": "RA_graphql_node_id",
        "apiUrl": "https://api.github.com/repos/owner/repository/releases/assets/479719693",
        "name": "audio-test.m4a",
        "url": "https://github.com/owner/repository/releases/download/book-test/audio-test.m4a",
    }
    verified = publisher._verify_asset(asset, sha256(content).hexdigest())
    assert verified.asset_id == 479719693
    assert verified.url.endswith("/audio-test.m4a")
    assert verified.byte_size == len(content)
    assert verified.created is True


def test_real_gh_asset_shape_from_stage_zero_is_parseable() -> None:
    payload = json.loads("""{
      "id":"RA_kwDOTa8iv84cl_EN",
      "apiUrl":"https://api.github.com/repos/MKasumi1007/free-cross-device-audiobook/releases/assets/479719693",
      "name":"public-domain-sine-probe.m4a",
      "url":"https://github.com/MKasumi1007/free-cross-device-audiobook/releases/download/stage0-release-probe/public-domain-sine-probe.m4a"
    }""")
    assert int(payload["apiUrl"].rsplit("/", 1)[-1]) == 479719693
