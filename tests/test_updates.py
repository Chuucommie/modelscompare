import hashlib
from pathlib import Path
import zipfile

import pytest
import requests

from modelscompare.updates import (
    ReleaseAsset,
    UpdateError,
    UpdateInfo,
    check_for_update,
    download_asset,
    extract_update,
    install_update,
    is_newer_version,
    select_asset,
)


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, text="", content=b""):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self._content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=1024):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _release_payload() -> dict:
    return {
        "tag_name": "v0.3.0",
        "html_url": "https://github.com/example/modelscompare/releases/tag/v0.3.0",
        "body": "Novità",
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [
            {
                "name": "modelscompare-linux-x86_64.tar.gz",
                "browser_download_url": "https://example.test/linux.tar.gz",
                "size": 10,
                "digest": "sha256:abc",
                "content_type": "application/gzip",
            },
            {
                "name": "modelscompare-windows-x64.zip",
                "browser_download_url": "https://example.test/windows.zip",
                "size": 20,
                "digest": "",
                "content_type": "application/zip",
            },
            {
                "name": "modelscompare-macos-arm64.tar.gz",
                "browser_download_url": "https://example.test/macos.tar.gz",
                "size": 30,
                "digest": "",
                "content_type": "application/gzip",
            },
        ],
    }


def test_version_comparison_handles_v_prefix_and_prereleases() -> None:
    assert is_newer_version("v0.10.0", "0.9.9")
    assert not is_newer_version("0.2.0", "v0.2.0")
    assert not is_newer_version("0.2.0-beta", "0.2.0")
    assert is_newer_version("0.2.1-beta", "0.2.0")


def test_check_for_update_parses_github_release() -> None:
    info = check_for_update("0.2.0", session=FakeSession(FakeResponse(_release_payload())))

    assert info is not None
    assert info.latest == "v0.3.0"
    assert info.version_label == "0.3.0"
    assert info.is_newer
    assert [asset.name for asset in info.assets] == [
        "modelscompare-linux-x86_64.tar.gz",
        "modelscompare-windows-x64.zip",
        "modelscompare-macos-arm64.tar.gz",
    ]


def test_check_for_update_returns_none_when_repository_has_no_release() -> None:
    assert check_for_update(session=FakeSession(FakeResponse(status_code=404))) is None


def test_check_for_update_reports_network_errors() -> None:
    session = FakeSession(FakeResponse(status_code=500))

    with pytest.raises(UpdateError, match="release"):
        check_for_update(session=session)


def test_select_asset_matches_operating_system_and_architecture() -> None:
    assets = [
        ReleaseAsset("modelscompare-linux-x86_64.tar.gz", "https://example.test/linux"),
        ReleaseAsset("modelscompare-macos-arm64.tar.gz", "https://example.test/macos"),
        ReleaseAsset("modelscompare-windows-x64.zip", "https://example.test/windows"),
    ]

    assert select_asset(assets, system="Linux", machine="x86_64").name == "modelscompare-linux-x86_64.tar.gz"
    assert select_asset(assets, system="Darwin", machine="arm64").name == "modelscompare-macos-arm64.tar.gz"
    assert select_asset(assets, system="Windows", machine="AMD64").name == "modelscompare-windows-x64.zip"


def test_select_asset_does_not_choose_wrong_architecture() -> None:
    assets = [
        ReleaseAsset("modelscompare-macos-arm64.tar.gz", "https://example.test/macos-arm64"),
        ReleaseAsset("modelscompare-macos-x64.tar.gz", "https://example.test/macos-x64"),
    ]

    assert select_asset(assets, system="Darwin", machine="arm64").name == "modelscompare-macos-arm64.tar.gz"
    assert select_asset(assets, system="Darwin", machine="x86_64").name == "modelscompare-macos-x64.tar.gz"
    assert select_asset(
        [ReleaseAsset("modelscompare-macos-arm64.tar.gz", "https://example.test/macos-arm64")],
        system="Darwin",
        machine="x86_64",
    ) is None


def test_download_asset_verifies_sha256_and_writes_file(tmp_path) -> None:
    content = b"portable-binary"
    asset = ReleaseAsset(
        "modelscompare-linux-x86_64.tar.gz",
        "https://example.test/linux.tar.gz",
        size=len(content),
        digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )

    path = download_asset(asset, tmp_path, session=FakeSession(FakeResponse(content=content)))

    assert path.read_bytes() == content
    assert not path.with_name(path.name + ".part").exists()


def test_download_asset_rejects_wrong_checksum(tmp_path) -> None:
    asset = ReleaseAsset(
        "modelscompare-linux-x86_64.tar.gz",
        "https://example.test/linux.tar.gz",
        size=4,
        digest="sha256:" + "0" * 64,
    )

    with pytest.raises(UpdateError, match="checksum"):
        download_asset(asset, tmp_path, session=FakeSession(FakeResponse(content=b"data")))

    assert not any(tmp_path.iterdir())


def test_source_install_returns_instructions_instead_of_replacing_python(tmp_path) -> None:
    info = UpdateInfo(
        current="0.2.0",
        latest="v0.3.0",
        tag="v0.3.0",
        html_url="https://example.test/release",
        assets=(ReleaseAsset("modelscompare-linux-x86_64.tar.gz", "https://example.test/linux"),),
        is_newer=True,
    )

    result = install_update(info, cache_dir=tmp_path, current_executable=Path("/usr/bin/modelscompare"))

    assert not result.scheduled
    assert "pip" in result.message
    assert not any(tmp_path.iterdir())


def test_extract_update_finds_the_executable_and_blocks_path_traversal(tmp_path) -> None:
    archive = tmp_path / "modelscompare-linux-x64.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("modelscompare-linux-x64/modelscompare", b"#!/bin/sh\n")
        bundle.writestr("README.md", "docs")

    binary = extract_update(archive, tmp_path / "unpacked")
    assert binary.name == "modelscompare"

    malicious = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious, "w") as bundle:
        bundle.writestr("../escape", b"bad")

    with pytest.raises(UpdateError, match="percorso non sicuro"):
        extract_update(malicious, tmp_path / "malicious")
