#!/usr/bin/env python3
"""Build standalone ModelsCompare executables and release archives.

Runs identically on Windows, macOS, and Linux.  Each archive is accompanied
by a ``.sha256`` file so the in-app updater can verify downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PORTABLE_README = ROOT / "PORTABLE-README.md"


def platform_details(
    system: str | None = None,
    machine: str | None = None,
) -> tuple[str, str, str]:
    system_name = (system or platform.system()).casefold()
    machine_name = (machine or platform.machine()).casefold()

    if system_name == "windows":
        os_slug = "windows"
        binary_name = "ModelsCompare.exe"
    elif system_name == "darwin":
        os_slug = "macos"
        binary_name = "modelscompare"
    else:
        os_slug = "linux"
        binary_name = "modelscompare"

    if machine_name in {"x86_64", "amd64", "x64"}:
        arch_slug = "x64"
    elif machine_name in {"arm64", "aarch64"}:
        arch_slug = "arm64"
    else:
        arch_slug = machine_name or "unknown"

    return os_slug, arch_slug, binary_name


def _run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksum(path: Path) -> Path:
    checksum_path = path.with_name(path.name + ".sha256")
    checksum_path.write_text(f"{_sha256(path)}  {path.name}\n", encoding="utf-8")
    return checksum_path


def _make_windows_zip(binary: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(binary, binary.name)
        bundle.write(PORTABLE_README, "README.md")


def _make_posix_tarball(binary: Path, archive: Path) -> None:
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(binary, arcname=binary.name)
        bundle.add(PORTABLE_README, arcname="README.md")


def build(skip_install: bool = False) -> Path:
    if not skip_install:
        _run([sys.executable, "-m", "pip", "install", "-e", ".[build]"])

    os_slug, arch_slug, binary_name = platform_details()
    DIST.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onefile",
            "--console",
            "--name",
            Path(binary_name).stem,
            "--paths",
            "src",
            "--specpath",
            str(BUILD / "pyinstaller"),
            "--collect-all",
            "prompt_toolkit",
            "--collect-all",
            "rich",
            "--collect-all",
            "openpyxl",
            "--collect-all",
            "bs4",
            "--collect-all",
            "requests",
            "--collect-all",
            "certifi",
            "src/modelscompare/cli.py",
        ]
    )

    built_binary = DIST / binary_name
    if not built_binary.exists():
        raise SystemExit(f"PyInstaller did not produce {built_binary}")
    if os.name != "nt":
        built_binary.chmod(0o755)

    archive_name = f"modelscompare-{os_slug}-{arch_slug}"
    if os_slug == "windows":
        archive = DIST / f"{archive_name}.zip"
        _make_windows_zip(built_binary, archive)
    else:
        archive = DIST / f"{archive_name}.tar.gz"
        _make_posix_tarball(built_binary, archive)

    checksum = _write_checksum(archive)
    print(f"Built: {built_binary}")
    print(f"Archive: {archive}")
    print(f"Checksum: {checksum}")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="do not run pip install before invoking PyInstaller",
    )
    args = parser.parse_args()
    build(skip_install=args.skip_install)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
